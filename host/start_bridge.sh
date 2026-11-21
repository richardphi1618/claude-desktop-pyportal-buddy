#!/usr/bin/env bash
# Start the Windows-side PyPortal bridge (if not already running).
#   ./start_bridge.sh          start in background (used by the hook)
#   ./start_bridge.sh --stop   stop it
#   ./start_bridge.sh --fg     run in the foreground (debugging)
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
SPOOL_DIR="$HOME/.claude/pyportal"
SPOOL="$SPOOL_DIR/events.jsonl"
LOG="$SPOOL_DIR/bridge.log"
mkdir -p "$SPOOL_DIR"
touch "$SPOOL"

running() { pgrep -f "python.exe -u .*pyportal_bridge.py" >/dev/null; }

if [ "${1:-}" = "--stop" ]; then
    pkill -f "python.exe -u .*pyportal_bridge.py"
    pkill -f "tail -n 200 -F $SPOOL"
    exit 0
fi

running && exit 0

WIN_SCRIPT="$(wslpath -w "$DIR/pyportal_bridge.py")"
CMD="tail -n 200 -F '$SPOOL' 2>/dev/null | python.exe -u '$WIN_SCRIPT'"

if [ "${1:-}" = "--fg" ]; then
    exec bash -c "$CMD"
fi

# keep the log small
[ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt 1048576 ] && mv "$LOG" "$LOG.1"
nohup setsid bash -c "$CMD" >>"$LOG" 2>&1 </dev/null &
