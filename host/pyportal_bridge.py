"""PyPortal bridge -- runs on *Windows* Python (needs pyserial).

Reads hook events (JSON lines) on stdin, keeps a small model of what each
Claude Code session is doing, and streams state snapshots to the PyPortal over
its USB serial console. Also logs whatever the PyPortal prints.

Also watches Windows' now-playing info (System Media Transport Controls) and
adds the current Spotify track to the snapshot as "mu": {"t": title, "a": artist}.
That part needs:  python.exe -m pip install winrt-Windows.Media.Control
                  winrt-Windows.Foundation winrt-Windows.Foundation.Collections

Also fetches the current weather from OpenWeather (the PyPortal has no Wi-Fi in
use) and adds it as "wx", plus the local time as "tm": [y, mo, d, h, mi, s] so
the device can set its clock. The weather needs host/weather_config.json:
    {"token": "<openweather api key>", "location": "Richmond,US", "units": "imperial"}

Launched by start_bridge.sh as:  tail -F events.jsonl | python.exe -u this.py
"""
import asyncio
import json
import queue
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import serial
from serial.tools import list_ports

ADAFRUIT_VID = 0x239A
HEARTBEAT = 5.0
RECONNECT = 3.0
FORGET_AFTER = 4 * 3600
MUSIC_POLL = 1.5
MUSIC_APPS = ("spotify",)     # substrings of the player's app id (lower case)
WEATHER_EVERY = 600
WEATHER_RETRY = 60
WEATHER_CONFIG = Path(__file__).with_name("weather_config.json")
MIN_HOLD = 5.0                # s a session keeps the screen before an equal one takes it
DONE_HOLD = 10.0              # s a finished session is shown before it counts as seen
IDLE_SHOWN = 60               # s the device keeps showing a done/waiting session (IDLE_TIMEOUT)
WAIT_NTYPES = ("idle_prompt", "elicitation_dialog")
LIVE_FOR = 600                # s without events before a session drops off the dots
MAX_DOTS = 8
IDLE_MODES = ("done", "waiting", "bye")
OVERRIDE = 30.0               # s a tap on the screen overrides the automatic choice
TAP_LINE = "@tap"             # what the PyPortal prints when the screen is tapped
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
    def __init__(self, sid, now):
        self.sid = sid
        self.start = now
        self.last = now
        self.mode = "hello"
        self.detail = ""
        self.tools = 0
        self.project = ""
        self.ended = False
        self.pending = ""
        self.cmd = ""
        self.unseen = False       # finished, but not yet shown on screen for DONE_HOLD
        self.shown_done = None    # when its "All done" first reached the screen

    def rank(self, now):
        """How much this session wants the screen (higher wins).

        Done/waiting only count while the device would still show them; after
        that the device falls back to weather, so they must not outrank work.
        """
        if self.mode == "permission":
            return 4
        fresh = now - self.last < IDLE_SHOWN
        if self.mode == "done" and self.unseen and fresh:
            return 3
        if self.mode == "waiting" and fresh:
            return 2
        if self.mode in ("done", "bye", "waiting"):
            return 0
        return 1                  # hello, thinking, tools, compacting

    def dot(self):
        """One-letter state for the session dots on the device."""
        if self.mode == "permission":
            return "p"
        if self.mode == "done":
            return "d" if self.unseen else "i"
        if self.mode == "waiting":
            return "w"
        if self.mode == "bye":
            return "i"
        return "b"


class Model:
    def __init__(self):
        self.sessions = {}
        self.music = None
        self.weather = None
        self.focus = None         # sid currently on screen
        self.focus_since = 0.0
        self.override_until = 0.0  # while in the future, a tap chose the focus

    def apply(self, ev):
        if "_music" in ev:
            self.music = ev["_music"]
            return
        if "_weather" in ev:
            self.weather = ev["_weather"]
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
            s = self.sessions[sid] = Session(sid, now)
        if name != "Stop":
            s.unseen = False
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
            ntype = ev.get("ntype", "")
            if ntype == "permission_prompt" or not ntype and "permission" in msg.lower():
                s.mode = "permission"
                s.detail = ("OK? " + s.pending) if s.pending else msg
            elif ntype in WAIT_NTYPES or not ntype:
                s.mode, s.detail = "waiting", msg or "waiting for you"
            # other types (agent_completed, auth_success, ...) don't need you
        elif name == "Stop":
            s.mode, s.detail = "done", "%d tools used" % s.tools
            s.unseen, s.shown_done = True, None
        elif name == "PreCompact":
            s.mode, s.detail = "compact", "tidying up context"
        elif name == "SessionEnd":
            s.mode, s.detail, s.ended = "bye", "session closed", True

    def snapshot(self):
        now = time.time()
        for sid in [k for k, v in self.sessions.items() if now - v.last > FORGET_AFTER]:
            del self.sessions[sid]
        if not self.sessions:
            snap = {"m": "bye", "age": 99999}     # no Claude: music/weather only
        else:
            snap = self._claude_snapshot(now)
        if self.music:
            snap["mu"] = self.music
        if self.weather:
            snap["wx"] = self.weather
        snap["tm"] = list(time.localtime(now)[:6])
        return snap

    def _live(self, now, pick=None):
        """Sessions shown as dots (and cycled by taps), oldest first."""
        return sorted((s for s in self.sessions.values()
                       if not s.ended and (now - s.last < LIVE_FOR or s.mode == "permission")
                       or s is pick),
                      key=lambda s: s.start)[:MAX_DOTS]

    def tap(self, now):
        """The screen was tapped: show the next session for OVERRIDE seconds."""
        cur = self.sessions.get(self.focus)
        live = self._live(now, cur)
        if len(live) < 2:
            return False
        i = live.index(cur) if cur in live else -1
        nxt = live[(i + 1) % len(live)]
        self.focus, self.focus_since = nxt.sid, now
        self.override_until = now + OVERRIDE
        return True

    def _choose(self, now):
        """Pick the session to show: highest rank, but hold the current one a while."""
        for s in self.sessions.values():          # stale prompts stop being urgent
            if s.mode == "permission" and now - s.last > 1800:
                s.mode = "waiting"
        if self.override_until:
            if now < self.override_until and self.focus in self.sessions:
                return self._mark_seen(self.sessions[self.focus], now)
            self.override_until = 0.0             # back to automatic, with a fresh hold
            self.focus_since = now
        best = max(self.sessions, key=lambda k: (self.sessions[k].rank(now), self.sessions[k].last))
        cur = self.sessions.get(self.focus)
        if cur is not None and best != self.focus:
            held = now - self.focus_since
            if cur.rank(now) > self.sessions[best].rank(now):
                best = self.focus                 # still the most important one
            elif cur.rank(now) == self.sessions[best].rank(now) and held < MIN_HOLD:
                best = self.focus                 # equal: let it finish its turn
        if best != self.focus:
            self.focus, self.focus_since = best, now
        return self._mark_seen(self.sessions[best], now)

    @staticmethod
    def _mark_seen(pick, now):
        if pick.unseen and pick.mode == "done":
            if pick.shown_done is None:
                pick.shown_done = now
            elif now - pick.shown_done >= DONE_HOLD:
                pick.unseen = False
        return pick

    @staticmethod
    def _cooler_snapshot(now, live):
        """Every live session is idle: the minis gather at the water cooler."""
        counts = {}
        for s in live:
            name = s.project or "Claude Code"
            counts[name] = counts.get(name, 0) + 1
        names = ", ".join(n if c == 1 else "%s x%d" % (n, c) for n, c in counts.items())
        return {
            "m": "cooler",
            "d": ascii_only(names),
            "p": "%d sessions idle" % len(live),
            "age": int(min(now - s.last for s in live)),   # weather after the last one rests
            "ss": len(live),
            "sd": "".join(s.dot() for s in live),
            "sf": -1,
        }

    def next_change(self):
        """Time at which a hold runs out and the screen may need to switch."""
        if self.override_until:
            return self.override_until
        cur = self.sessions.get(self.focus)
        if cur is None or len(self.sessions) < 2 and not cur.unseen:
            return None
        if cur.unseen and cur.shown_done is not None:
            return cur.shown_done + DONE_HOLD
        return self.focus_since + MIN_HOLD

    def _claude_snapshot(self, now):
        pick = self._choose(now)
        live = self._live(now, pick)
        if (not self.override_until and len(live) >= 2
                and all(s.mode in IDLE_MODES and not s.unseen for s in live)):
            return self._cooler_snapshot(now, live)
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
        if len(live) > 1:
            snap["sd"] = "".join(s.dot() for s in live)
            snap["sf"] = live.index(pick) if pick in live else -1
        if self.override_until > now:
            snap["ov"] = int(self.override_until - now + 0.999)
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


def load_weather_config():
    try:
        cfg = json.loads(WEATHER_CONFIG.read_text())
    except FileNotFoundError:
        return None
    if not cfg.get("token"):
        return None
    cfg.setdefault("location", "Richmond,US")
    cfg.setdefault("units", "imperial")
    return cfg


def fetch_weather(cfg):
    query = urllib.parse.urlencode({"q": cfg["location"], "units": cfg["units"],
                                    "appid": cfg["token"]})
    url = "https://api.openweathermap.org/data/2.5/weather?" + query
    with urllib.request.urlopen(url, timeout=15) as resp:
        w = json.load(resp)
    cond = w["weather"][0]
    desc = cond.get("description", "")
    return {
        "i": cond.get("icon", ""),
        "c": ascii_only("%s, %s" % (w.get("name", ""), w.get("sys", {}).get("country", "")))[:30],
        "w": ascii_only(cond.get("main", ""))[:20],
        "d": ascii_only(desc[:1].upper() + desc[1:])[:40],
        "t": round(w["main"]["temp"]),
        "u": {"imperial": "F", "metric": "C"}.get(cfg["units"], "K"),
    }


def weather_watcher(q):
    cfg = load_weather_config()
    if cfg is None:
        log("weather: no token in", WEATHER_CONFIG, "- weather display disabled")
        return
    log("weather: watching", cfg["location"])
    while True:
        try:
            wx = fetch_weather(cfg)
            log("weather:", wx)
            q.put({"_weather": wx})
            delay = WEATHER_EVERY
        except Exception as e:  # pylint: disable=broad-except
            log("weather: fetch failed:", type(e).__name__, e)   # never log the URL (has the key)
            delay = WEATHER_RETRY
        time.sleep(delay)


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
    threading.Thread(target=weather_watcher, args=(q,), daemon=True).start()
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
            due = model.next_change()
            if due is not None and last_sent < due <= now:
                dirty = True
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
                    if text.endswith(TAP_LINE):
                        switched = model.tap(time.time())
                        log("tap:", "switched" if switched else "ignored (one session)")
                        dirty = dirty or switched
                    elif text:
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
