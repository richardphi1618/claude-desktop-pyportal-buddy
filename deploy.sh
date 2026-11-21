#!/usr/bin/env bash
# Copy device/ files to the PyPortal's CIRCUITPY drive (found by volume label via Windows).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
DRIVE=$(powershell.exe -NoProfile -Command "(Get-Volume -FileSystemLabel CIRCUITPY).DriveLetter" | tr -d '\r\n ')
[ -n "$DRIVE" ] || { echo "CIRCUITPY drive not found"; exit 1; }
for f in claude_buddy.py weather_view.py code.py; do     # code.py last: the copy triggers a reload
    src="$(wslpath -w "$DIR/device/$f")"
    powershell.exe -NoProfile -Command "Copy-Item -LiteralPath '$src' -Destination '${DRIVE}:\\$f' -Force"
    echo "copied $f -> ${DRIVE}:\\$f"
done
