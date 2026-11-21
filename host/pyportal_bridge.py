"""PyPortal bridge -- runs on *Windows* Python (needs pyserial).

Reads hook events (JSON lines) on stdin, keeps a small model of what each
Claude Code session is doing, and streams state snapshots to the PyPortal over
its USB serial console. Also logs whatever the PyPortal prints.

Also watches Windows' now-playing info (System Media Transport Controls) and
adds the current Spotify track to the snapshot as "mu": {"t": title, "a": artist}.
That part needs:  python.exe -m pip install winrt-Windows.Media.Control
                  winrt-Windows.Foundation winrt-Windows.Foundation.Collections

Launched by start_bridge.sh as:  tail -F events.jsonl | python.exe -u this.py
"""
import asyncio
import json
import queue
import re
import sys
import threading
import time

import serial
from serial.tools import list_ports

ADAFRUIT_VID = 0x239A
HEARTBEAT = 5.0
RECONNECT = 3.0
FORGET_AFTER = 4 * 3600
MUSIC_POLL = 1.5
MUSIC_APPS = ("spotify",)     # substrings of the player's app id (lower case)
PLAYING = 4                   # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING

READ_TOOLS = {"Read", "Grep", "Glob", "LS", "NotebookRead", "ListMcpResourcesTool",
              "ReadMcpResourceTool"}
WEB_TOOLS = {"WebFetch", "WebSearch"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
BASH_TOOLS = {"Bash", "BashOutput", "KillShell", "KillBash", "Monitor"}
AGENT_TOOLS = {"Agent", "Task", "SendMessage"}
PLAN_TOOLS = {"TodoWrite", "EnterPlanMode", "ExitPlanMode", "TaskCreate", "TaskUpdate",
              "TaskList", "AskUserQuestion"}


ANSI = re.compile(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b\[[0-9;]*[A-Za-z]")


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def tool_mode(tool):
    if tool in READ_TOOLS:
        return "read"
    if tool in WEB_TOOLS:
        return "web"
    if tool in EDIT_TOOLS:
        return "edit"
    if tool in BASH_TOOLS:
        return "bash"
    if tool in AGENT_TOOLS:
        return "agent"
    if tool in PLAN_TOOLS:
        return "plan"
    return "tool"


def tool_label(tool, detail):
    if tool.startswith("mcp__"):
        tool = tool.split("__")[-1]
    return (tool + " " + detail).strip() if detail else tool


class Session:
    def __init__(self, now):
        self.start = now
        self.last = now
        self.mode = "hello"
        self.detail = ""
        self.tools = 0
        self.project = ""
        self.ended = False
        self.pending = ""
        self.cmd = ""


class Model:
    def __init__(self):
        self.sessions = {}
        self.music = None

    def apply(self, ev):
        if "_music" in ev:
            self.music = ev["_music"]
            return
        name = ev.get("ev", "")
        if name == "SubagentStop":
            # Claude Code also fires this for its own background helpers, often
            # after Stop. Real subagents end with PostToolUse(Agent) anyway, so
            # it carries no information and used to flip "done" back to "thinking".
            return
        now = min(float(ev.get("ts") or time.time()), time.time())
        sid = ev.get("sid") or "?"
        s = self.sessions.get(sid)
        if s is None or name == "SessionStart":
            s = self.sessions[sid] = Session(now)
        s.last = now
        s.ended = False
        s.project = ev.get("proj") or s.project
        tool = ev.get("tool", "")
        label = tool_label(tool, ev.get("detail", "")) if tool else ""

        if name == "SessionStart":
            s.mode, s.detail = "hello", "ready when you are"
        elif name == "UserPromptSubmit":
            s.mode, s.detail = "thinking", "on it..."
        elif name == "PreToolUse":
            s.tools += 1
            s.mode, s.detail, s.pending = tool_mode(tool), label, label
            s.cmd = ev.get("cmd", "")
        elif name == "PostToolUse":
            s.mode = "thinking"
        elif name == "Notification":
            msg = ev.get("msg", "")
            if ev.get("ntype") == "permission_prompt" or "permission" in msg.lower():
                s.mode = "permission"
                s.detail = ("OK? " + s.pending) if s.pending else msg
            else:
                s.mode, s.detail = "waiting", msg or "waiting for you"
        elif name == "Stop":
            s.mode, s.detail = "done", "%d tools used" % s.tools
        elif name == "PreCompact":
            s.mode, s.detail = "compact", "tidying up context"
        elif name == "SessionEnd":
            s.mode, s.detail, s.ended = "bye", "session closed", True

    def snapshot(self):
        now = time.time()
        for sid in [k for k, v in self.sessions.items() if now - v.last > FORGET_AFTER]:
            del self.sessions[sid]
        if not self.sessions:
            if self.music is None:
                return None
            snap = {"m": "bye", "age": 99999}     # no Claude, just music
        else:
            snap = self._claude_snapshot(now)
        if self.music:
            snap["mu"] = self.music
        return snap

    def _claude_snapshot(self, now):
        live = [s for s in self.sessions.values() if not s.ended and now - s.last < 600]
        urgent = [s for s in self.sessions.values()
                  if s.mode == "permission" and now - s.last < 1800]
        pick = max(urgent or self.sessions.values(), key=lambda s: s.last)
        snap = {
            "m": pick.mode,
            "d": ascii_only(pick.detail),
            "p": ascii_only(pick.project),
            "n": pick.tools,
            "el": int(now - pick.start),
            "age": int(now - pick.last),
            "ss": max(len(live), 1),
        }
        if pick.mode == "bash" and pick.cmd:
            snap["c"] = ascii_only(pick.cmd)[:60]
        return snap


def ascii_only(text):
    return str(text).encode("ascii", "replace").decode("ascii")


def find_port():
    for p in sorted(list_ports.comports(), key=lambda p: p.device):
        if p.vid == ADAFRUIT_VID:
            return p.device
    return None


async def _music_loop(q):
    from winrt.windows.media.control import (  # pylint: disable=import-outside-toplevel
        GlobalSystemMediaTransportControlsSessionManager as Manager)
    mgr = await Manager.request_async()
    log("music: watching for", ", ".join(MUSIC_APPS))
    last = None
    while True:
        now_playing = {}
        try:
            for sess in mgr.get_sessions():
                app = (sess.source_app_user_model_id or "").lower()
                if not any(a in app for a in MUSIC_APPS):
                    continue
                if sess.get_playback_info().playback_status != PLAYING:
                    continue
                props = await sess.try_get_media_properties_async()
                if props.title:
                    now_playing = {"t": ascii_only(props.title)[:60],
                                   "a": ascii_only(props.artist or "")[:60]}
                    break
        except Exception as e:  # pylint: disable=broad-except
            log("music: poll failed:", e)
        if now_playing != last:
            last = now_playing
            log("music:", now_playing or "stopped")
            q.put({"_music": now_playing})
        await asyncio.sleep(MUSIC_POLL)


def music_watcher(q):
    try:
        asyncio.run(_music_loop(q))
    except ImportError:
        log("music: winrt packages not installed, Spotify display disabled")
    except Exception as e:  # pylint: disable=broad-except
        log("music: watcher stopped:", e)


def stdin_reader(q):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            q.put(json.loads(line))
        except ValueError:
            pass
    q.put(None)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    q = queue.Queue()
    threading.Thread(target=stdin_reader, args=(q,), daemon=True).start()
    threading.Thread(target=music_watcher, args=(q,), daemon=True).start()
    model = Model()
    ser = None
    next_try = 0.0
    last_sent = 0.0
    dirty = False
    rx = b""
    log("bridge started")

    while True:
        now = time.time()
        if ser is None and now >= next_try:
            port = find_port()
            if port:
                try:
                    ser = serial.Serial(port, 115200, timeout=0, write_timeout=1)
                    log("connected to", port)
                    dirty = True
                except (serial.SerialException, OSError) as e:
                    log("open failed:", e)
            next_try = now + RECONNECT

        try:
            ev = q.get(timeout=0.1)
            while True:
                if ev is None:
                    log("stdin closed, exiting")
                    return
                model.apply(ev)
                dirty = True
                ev = q.get_nowait()
        except queue.Empty:
            pass

        if ser is None:
            continue
        try:
            if dirty or now - last_sent >= HEARTBEAT:
                snap = model.snapshot()
                if snap is not None:
                    line = json.dumps(snap, separators=(",", ":"), ensure_ascii=True)
                    ser.write((line + "\n").encode("ascii"))
                    if dirty:
                        log("->", line)
                last_sent = now
                dirty = False
            waiting = ser.in_waiting
            if waiting:
                rx += ser.read(waiting)
                *lines, rx = rx.split(b"\n")
                for ln in lines:
                    text = ANSI.sub("", ln.decode("utf-8", "replace")).strip()
                    if text:
                        log("pyportal:", text)
                rx = rx[-2000:]
        except (serial.SerialException, OSError) as e:
            log("serial error:", e)
            try:
                ser.close()
            except Exception:  # pylint: disable=broad-except
                pass
            ser = None
            next_try = now + RECONNECT


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
