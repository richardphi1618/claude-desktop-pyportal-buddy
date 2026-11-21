# Claude Code buddy + OpenWeather display for the Adafruit PyPortal.
#
# Shows an animated Claude buddy while Claude Code is active (state arrives as
# JSON lines over USB serial from host/pyportal_bridge.py), and falls back to
# the original OpenWeather screen when Claude has been idle for a while.
# While Spotify plays, the buddy wears headphones; if Claude is idle it shows a
# "listening" screen with the song and artist instead of the weather.
import gc
import sys
import json
import time
import board
import displayio
import neopixel
import supervisor
from adafruit_pyportal import PyPortal

cwd = ("/" + __file__).rsplit('/', 1)[0]
sys.path.append(cwd)
import openweather_graphics  # pylint: disable=wrong-import-position
from claude_buddy import ClaudeBuddy, BUSY_MODES  # pylint: disable=wrong-import-position

try:
    from secrets import secrets
except ImportError:
    print("WiFi secrets are kept in secrets.py, please add them there!")
    raise

# ---- settings ---------------------------------------------------------
LOCATION = "Richmond, US"
IDLE_TIMEOUT = 60         # s after Claude finishes before showing weather
BUSY_TIMEOUT = 1800       # s a busy/permission state may stay up without news
THINK_TIMEOUT = 300       # s "thinking" may stay up without news (e.g. after Esc)
BRIDGE_TIMEOUT = 20       # s of serial silence => bridge gone => weather
WEATHER_EVERY = 600
CLOCK_SYNC_EVERY = 3600
RETRY_AFTER = 60

DATA_SOURCE = "http://api.openweathermap.org/data/2.5/weather?q=" + LOCATION
DATA_SOURCE += "&appid=" + secrets['openweather_token']

display = board.DISPLAY
display.rotation = 0

pyportal = PyPortal(url=DATA_SOURCE, json_path=[], default_bg=0x000000)
pixel = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.15)
pixel[0] = 0

root = displayio.Group()
display.root_group = root
root.append(pyportal.splash)

gfx = openweather_graphics.OpenWeather_Graphics(pyportal.splash, am_pm=True, celsius=False)
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
    age = snap.get("age", 0) + (now - rx)
    mode = snap.get("m")
    if mode == "thinking":
        return age < THINK_TIMEOUT
    if mode == "permission" or mode in BUSY_MODES:
        return age < BUSY_TIMEOUT
    if mode == "bye":
        return age < 30
    return age < IDLE_TIMEOUT


def music_playing(snap, rx, now):
    return bool(snap and snap.get("mu") and now - rx <= BRIDGE_TIMEOUT)


serial_in = SerialLines()
snap, snap_rx = None, 0.0
showing_claude = False
weather_at = clock_at = None
weather_retry = clock_retry = 0.0
clock_drawn = -100.0

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

        claude = claude_wanted(snap, snap_rx, now)
        music = music_playing(snap, snap_rx, now)
        buddy.set_music_view(music and not claude)
        want = claude or music
        if want != showing_claude:
            showing_claude = want
            print("screen:", "buddy" if want else "weather")
            buddy.hidden = not want
            pyportal.splash.hidden = want
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
            buddy.tick(now)
            pixel[0] = buddy.pixel_color(now)
            time.sleep(0.03)
            continue

        # ---- weather mode (network calls block, so only do them here) -----
        if (clock_at is None or now - clock_at > CLOCK_SYNC_EVERY) and now >= clock_retry:
            try:
                print("Getting time from internet!")
                pyportal.get_local_time()
                clock_at = now
            except Exception as e:  # pylint: disable=broad-except
                print("time sync failed:", e)
                clock_retry = now + RETRY_AFTER
        if (weather_at is None or now - weather_at > WEATHER_EVERY) and now >= weather_retry:
            try:
                value = pyportal.fetch()
                gfx.display_weather(value)
                weather_at = now
            except Exception as e:  # pylint: disable=broad-except
                print("weather fetch failed:", e)
                weather_retry = now + RETRY_AFTER
            gc.collect()
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
