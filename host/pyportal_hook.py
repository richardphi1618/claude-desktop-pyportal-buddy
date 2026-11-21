#!/usr/bin/python3
"""Claude Code hook: append a compact event line to the PyPortal spool file.

Registered for SessionStart, UserPromptSubmit, PreToolUse, PostToolUse,
Notification, Stop, SubagentStop, PreCompact and SessionEnd. Must be fast and
must never fail or print anything (exit 0 always), so it never gets in
Claude's way.
"""
import json
import os
import subprocess
import sys
import time

SPOOL_DIR = os.path.expanduser("~/.claude/pyportal")
SPOOL = os.path.join(SPOOL_DIR, "events.jsonl")
MAX_SPOOL = 512 * 1024
HERE = os.path.dirname(os.path.abspath(__file__))


def short(text, n=60):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "~"


def summarize(tool, ti):
    """A few words about what a tool call is touching."""
    for key in ("file_path", "notebook_path"):
        if ti.get(key):
            return os.path.basename(ti[key])
    if tool == "Bash":
        return short(ti.get("description") or ti.get("command", ""))
    if tool in ("Grep", "Glob"):
        return short(ti.get("pattern", ""))
    if tool == "WebFetch":
        url = ti.get("url", "")
        return short(url.split("//", 1)[-1].split("/", 1)[0])
    if tool == "WebSearch":
        return short(ti.get("query", ""))
    if tool in ("Agent", "Task"):
        return short(ti.get("description", ""))
    if tool == "Skill":
        return short(ti.get("skill", ""))
    return ""


def ensure_bridge():
    try:
        subprocess.Popen(
            [os.path.join(HERE, "start_bridge.sh")],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    ev = data.get("hook_event_name", "")
    out = {
        "ev": ev,
        "sid": (data.get("session_id") or "")[:12],
        "ts": round(time.time(), 2),
        "proj": os.path.basename((data.get("cwd") or "").rstrip("/")),
    }
    tool = data.get("tool_name")
    if tool:
        out["tool"] = tool
        out["detail"] = summarize(tool, data.get("tool_input") or {})
        if tool == "Bash":
            out["cmd"] = short((data.get("tool_input") or {}).get("command", ""), 80)
    if ev == "Notification":
        out["msg"] = short(data.get("message", ""), 80)
        out["ntype"] = data.get("notification_type", "")

    os.makedirs(SPOOL_DIR, exist_ok=True)
    try:
        if os.path.getsize(SPOOL) > MAX_SPOOL:
            os.truncate(SPOOL, 0)
    except OSError:
        pass
    with open(SPOOL, "a") as f:
        f.write(json.dumps(out) + "\n")

    if ev in ("SessionStart", "UserPromptSubmit"):
        ensure_bridge()


if __name__ == "__main__":
    try:
        main()
    except Exception:  # pylint: disable=broad-except
        pass
    sys.exit(0)
