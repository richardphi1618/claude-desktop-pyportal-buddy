# Claude Code buddy + weather display for the Adafruit PyPortal.
#
# Shows an animated Claude buddy while Claude Code is active (state arrives as
# JSON lines over USB serial from host/pyportal_bridge.py), and falls back to
# a weather screen when Claude has been idle for a while.
# No Wi-Fi: the bridge fetches the weather and sends it, plus the local time,
# over the same USB serial link.
# While Spotify plays, the buddy wears headphones; if Claude is idle it shows a
# "listening" screen with the song and artist instead of the weather.
import gc
import sys
import json
import time
import board
import displayio
import neopixel
import rtc
import supervisor
import adafruit_touchscreen

cwd = ("/" + __file__).rsplit('/', 1)[0]
sys.path.append(cwd)
from weather_view import WeatherView  # pylint: disable=wrong-import-position
from claude_buddy import ClaudeBuddy, BUSY_MODES  # pylint: disable=wrong-import-position

# ---- settings ---------------------------------------------------------
IDLE_TIMEOUT = 60         # s after Claude finishes before showing weather
BUSY_TIMEOUT = 1800       # s a busy/permission state may stay up without news
THINK_TIMEOUT = 300       # s "thinking" may stay up without news (e.g. after Esc)
BRIDGE_TIMEOUT = 20       # s of serial silence => bridge gone => weather
TOUCH_EVERY = 0.05        # s between touchscreen reads
TAP_GAP = 0.3             # s after a tap before another one counts

display = board.DISPLAY
display.rotation = 0

pixel = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.15)
pixel[0] = 0

root = displayio.Group()
display.root_group = root
gfx = WeatherView(am_pm=True)
root.append(gfx)
touch = adafruit_touchscreen.Touchscreen(
    board.TOUCH_XL, board.TOUCH_XR, board.TOUCH_YD, board.TOUCH_YU,
    calibration=((5200, 59000), (5800, 57000)), size=(320, 240))
buddy = ClaudeBuddy()
buddy.hidden = True
root.append(buddy)
gc.collect()
print("buddy ready, mem free:", gc.mem_free())


class SerialLines:
    """Non-blocking reader for JSON lines arriving on the USB console.

    Bytes go into fixed buffers (no string building per character), and only
    the newest complete line is parsed, to keep heap churn low.
    """

    MAX = 1024

    def __init__(self):
        self.buf = bytearray(self.MAX)
        self.n = 0
        self.line = bytearray(self.MAX)
        self.line_n = 0

    def poll(self):
        got = False
        budget = 4096
        while supervisor.runtime.serial_bytes_available and budget > 0:
            chunk = sys.stdin.read(min(supervisor.runtime.serial_bytes_available, 256))
            data = chunk.encode()                               # iterate ints, not 1-char strs
            budget -= len(data)
            for o in data:
                if o == 10:                                     # "\n"
                    if self.n and self.buf[0] == 0x7B:          # starts with "{"
                        self.buf, self.line = self.line, self.buf   # swap, no copy
                        self.line_n = self.n
                        got = True
                    self.n = 0
                elif self.n < self.MAX:
                    self.buf[self.n] = o if o < 128 else 0x3F   # "?"
                    self.n += 1
                else:
                    self.n = 0                                  # overlong: drop
        if not got:
            return None
        try:
            return json.loads(str(self.line[:self.line_n], "ascii"))
        except ValueError:
            return None


def claude_wanted(snap, rx, now):
    if snap is None or now - rx > BRIDGE_TIMEOUT:
        return False
    if now - rx < snap.get("ov", 0):      # a tap picked this session: keep it up
        return True
    age = snap.get("age", 0) + (now - rx)
    mode = snap.get("m")
    if mode == "thinking":
        return age < THINK_TIMEOUT
    if mode == "permission" or mode in BUSY_MODES:
        return age < BUSY_TIMEOUT
    if mode == "bye":
        return age < 30
    return age < IDLE_TIMEOUT


def sync_clock(tm):
    """Set the RTC from the bridge's [y, mo, d, h, mi, s] local time if it drifted."""
    t = time.struct_time((tm[0], tm[1], tm[2], tm[3], tm[4], tm[5], 0, -1, -1))
    if abs(time.mktime(t) - time.time()) > 2:
        rtc.RTC().datetime = t
        return True
    return False


def music_playing(snap, rx, now):
    return bool(snap and snap.get("mu") and now - rx <= BRIDGE_TIMEOUT)


serial_in = SerialLines()
snap, snap_rx = None, 0.0
showing_claude = False
clock_drawn = -100.0
touch_at = tap_at = 0.0
touching = False

gc_at = 0.0
mem_report_at = 0.0
mem_errors = []

while True:
    try:
        now = time.monotonic()
        s = serial_in.poll()
        if s is not None:
            snap, snap_rx = s, now
            buddy.update(s, now)
            if "wx" in s:
                gfx.show(s["wx"])
            if "tm" in s and sync_clock(s["tm"]):
                clock_drawn = -100.0

        claude = claude_wanted(snap, snap_rx, now)
        music = music_playing(snap, snap_rx, now)
        buddy.set_music_view(music and not claude)
        want = claude or music
        if want != showing_claude:
            showing_claude = want
            print("screen:", "buddy" if want else "weather")
            buddy.hidden = not want
            gfx.hidden = want
            if not want:
                pixel[0] = 0
                clock_drawn = -100.0

        if showing_claude:
            if now - gc_at > 1.0:                # small, regular clean-ups
                gc.collect()
                gc_at = now
                if now - mem_report_at > 300:
                    print("mem free:", gc.mem_free())
                    mem_report_at = now
            if claude and now - touch_at >= TOUCH_EVERY:   # taps only on Claude screens
                touch_at = now
                pressed = touch.touch_point is not None
                if pressed and not touching and now - tap_at >= TAP_GAP:
                    tap_at = now
                    print("@tap")                       # the bridge switches sessions
                touching = pressed
            buddy.tick(now)
            pixel[0] = buddy.pixel_color(now)
            time.sleep(0.03)
            continue

        # ---- weather mode: data and time arrive with the bridge snapshots ----
        if now - clock_drawn >= 30:
            gfx.update_time()
            clock_drawn = now
        time.sleep(0.1)
    except MemoryError as e:
        # heap too fragmented: tidy up and keep going; restart if it keeps happening
        gc.collect()
        now = time.monotonic()
        mem_errors = [t for t in mem_errors if now - t < 60] + [now]
        print("MemoryError:", e, "free:", gc.mem_free(), "count:", len(mem_errors))
        if len(mem_errors) >= 3:
            print("too many MemoryErrors, restarting code.py")
            supervisor.reload()
