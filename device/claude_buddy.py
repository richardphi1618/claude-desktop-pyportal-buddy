"""Animated "Claude buddy" screen for the PyPortal (320x240).

ClaudeBuddy is a displayio.Group. Feed it state snapshots from the host bridge
with update(), and call tick() every frame to animate.
"""
import math
import displayio
import terminalio
import vectorio
from adafruit_display_text.bitmap_label import Label

# ---- shapes ---------------------------------------------------------------
# vectorio shapes are drawn on the fly: ~100 bytes each, versus 0.5-1.5 KB for
# adafruit_display_shapes (which keeps a bitmap per shape). The PyPortal only
# has ~25 KB to spare once Wi-Fi is up, so this matters. Same call signatures
# as adafruit_display_shapes; outlines are drawn as a filled shape with an
# inset shape on top (black when there is no fill).
_PALS = {}


def pal(color):
    """Shared one-color palette; set shape.pixel_shader = pal(c) to recolor."""
    p = _PALS.get(color)
    if p is None:
        p = displayio.Palette(1)
        p[0] = color
        _PALS[color] = p
    return p


def _outlined(make, fill, outline, stroke):
    g = displayio.Group()
    g.append(make(0, outline))
    g.append(make(stroke, 0x000000 if fill is None else fill))
    return g


def Rect(x, y, w, h, fill=None, outline=None, stroke=1):  # pylint: disable=invalid-name
    if outline is not None:
        return _outlined(lambda i, c: Rect(x + i, y + i, w - 2 * i, h - 2 * i, fill=c),
                         fill, outline, stroke)
    return vectorio.Rectangle(pixel_shader=pal(fill), width=w, height=h, x=x, y=y)


def _rr_points(w, h, r):
    r = min(r, w // 2, h // 2)
    pts = []
    for cx, cy, a0 in ((w - 1 - r, r, 270), (w - 1 - r, h - 1 - r, 0),
                       (r, h - 1 - r, 90), (r, r, 180)):
        for k in range(4):
            a = math.radians(a0 + k * 30)
            pts.append((int(round(cx + r * math.cos(a))), int(round(cy + r * math.sin(a)))))
    return pts


def RoundRect(x, y, w, h, r, fill=None, outline=None, stroke=1):  # pylint: disable=invalid-name
    if outline is not None:
        return _outlined(lambda i, c: RoundRect(x + i, y + i, w - 2 * i, h - 2 * i,
                                                max(r - i, 0), fill=c),
                         fill, outline, stroke)
    return vectorio.Polygon(pixel_shader=pal(fill), points=_rr_points(w, h, r), x=x, y=y)


def Circle(cx, cy, r, fill=None, outline=None, stroke=1):  # pylint: disable=invalid-name
    """Note: a vectorio Circle's x/y is its CENTER."""
    if outline is not None:
        return _outlined(lambda i, c: Circle(cx, cy, r - i, fill=c), fill, outline, stroke)
    return vectorio.Circle(pixel_shader=pal(fill), radius=r, x=cx, y=cy)


def Triangle(x0, y0, x1, y1, x2, y2, fill=None):  # pylint: disable=invalid-name
    return vectorio.Polygon(pixel_shader=pal(fill), points=[(x0, y0), (x1, y1), (x2, y2)],
                            x=0, y=0)

ORANGE = 0xD97757
ORANGE_DK = 0xA9553A
INK = 0x1B1B1F
WHITE = 0xF2F0EA
GREY = 0x8A8F98
DIM = 0x5A606A
YELLOW = 0xFFD54F
GREEN = 0x1DB954          # Spotify green
PHONES = 0x2B2F36

# mode -> (verb, color, prop, eyes, mouth, bob_speed, bob_amp)
MODES = {
    "hello":      ("Hello!",      ORANGE,   "wave",    "happy",  "smile", 3.0, 3),
    "thinking":   ("Thinking",    0xB39DDB, "bubble",  "open",   "flat",  2.0, 2),
    "read":       ("Reading",     0x4FC3F7, "page",    "open",   "flat",  4.0, 2),
    "web":        ("Browsing",    0x4DD0E1, "globe",   "open",   "flat",  4.0, 2),
    "edit":       ("Editing",     0xFFB74D, "page",    "open",   "flat",  4.0, 1),
    "bash":       ("Running",     0x81C784, "term",    "open",   "flat",  9.0, 1),
    "agent":      ("Delegating",  0xF06292, "minis",   "open",   "smile", 5.0, 3),
    "plan":       ("Planning",    0xAED581, "page",    "open",   "flat",  3.0, 1),
    "tool":       ("Using tool",  0x90A4AE, "spinner", "open",   "flat",  5.0, 2),
    "permission": ("Needs you!",  YELLOW,   "alert",   "open",   "o",    10.0, 4),
    "waiting":    ("Your turn",   0xFFF176, "alert",   "open",   "flat",  1.5, 2),
    "done":       ("All done",    0x66BB6A, "sparkle", "happy",  "smile", 1.5, 2),
    "compact":    ("Compacting",  0x9575CD, "spinner", "closed", "flat",  0.0, 0),
    "bye":        ("Goodnight",   0x78909C, "zzz",     "closed", "flat",  0.8, 1),
    "music":      ("Listening",   GREEN,    "notes",   "happy",  "smile", 5.0, 3),
}
# session dots in the header, one per live session (letters from the bridge's
# "sd" string); the one on screen is bigger. They replace the single state dot.
DOT_COLORS = {"p": YELLOW, "d": 0x66BB6A, "w": WHITE, "b": 0x4FC3F7, "i": DIM}
MAX_DOTS = 8
DOT_Y = 10
DOT_GAP = 12
DOT_RIGHT = 250           # x of the last dot (where the single state dot sits)

BUSY_MODES = ("thinking", "read", "web", "edit", "bash", "agent", "plan",
              "tool", "compact")

BX, BY = 110, 50          # buddy body origin (top-left)
SPIN = "|/-\\"
TERM_GREEN = 0x33FF66
CMDS = ("ls -la", "git diff", "pytest -q", "make", "grep TODO", "npm test")
CMD_CYCLE = 2.6           # s per typed command (type, then show output)
KEY_DT = 0.11             # s per keystroke
KB_X, KB_Y = 98, 116      # keyboard top-left (screen coords)
KEY_XS = tuple(KB_X + 7 + i * 13 for i in range(9))
TERM_X, TERM_Y = 214, 56  # terminal window (screen coords)
TERM_COLS, TERM_ROWS = 15, 3
MAX_CMD = TERM_COLS * TERM_ROWS - 3   # room for "$ " and the cursor
TYPE_TIME = 2.4           # real commands are typed in at most this long

PAGE_X, PAGE_Y = 240, 60  # page (screen coords)
PAGE_W = (32, 26, 30, 18, 28)   # length of each written line
LINE_T = 0.45             # s to write one line
PAGE_HOLD = 0.4           # s to admire a full page
PAGE_SLIDE = 0.35         # s to slide the page out (and a new one in)
WRITE_END = len(PAGE_W) * LINE_T
PAGE_CYCLE = WRITE_END + PAGE_HOLD + 2 * PAGE_SLIDE


def _label(text, color, scale=1, anchor=(0.0, 0.5), pos=(0, 0)):
    lb = Label(terminalio.FONT, text=text, color=color, scale=scale)
    lb.anchor_point = anchor
    lb.anchored_position = pos
    return lb


def _set_text(lb, text):
    if lb.text != text:
        lb.text = text


def _fmt_elapsed(sec):
    sec = int(sec)
    if sec < 60:
        return "%ds" % sec
    if sec < 3600:
        return "%dm" % (sec // 60)
    return "%dh%02dm" % (sec // 3600, (sec % 3600) // 60)


def _fit(text, n):
    return text if len(text) <= n else text[:n - 2] + ".."


def _marquee(text, n, now):
    """Scroll text that doesn't fit in n characters."""
    if len(text) <= n:
        return text
    loop = text + "   *   "
    i = int(now * 4) % len(loop)
    return (loop + loop)[i:i + n]


def _pencil():
    """Pencil pointing down-left; its tip is at (0, 21) in group coords."""
    g = displayio.Group()
    wood, yellow, pink = 0xF0C987, 0xFFC233, 0xF48FB1
    g.append(Triangle(1, 16, 19, 6, 15, 2, fill=yellow))
    g.append(Triangle(1, 16, 5, 20, 19, 6, fill=yellow))
    g.append(Triangle(0, 21, 1, 16, 5, 20, fill=wood))
    g.append(Rect(0, 19, 2, 2, fill=INK))
    g.append(Triangle(15, 2, 19, 6, 21, 4, fill=pink))
    g.append(Triangle(15, 2, 21, 4, 17, 0, fill=pink))
    g.append(RoundRect(6, 9, 11, 9, 4, fill=ORANGE_DK))   # the buddy's hand
    return g


def _note(color):
    g = displayio.Group()
    g.append(Circle(4, 14, 4, fill=color))
    g.append(Rect(7, 0, 2, 15, fill=color))
    g.append(Rect(7, 0, 7, 3, fill=color))
    return g


class ClaudeBuddy(displayio.Group):
    def __init__(self):
        super().__init__()
        self.mode = "hello"
        self.claude_mode = "hello"
        self.music = None         # {"t": title, "a": artist} while Spotify plays
        self.music_view = False   # True: Claude idle, show the listening screen
        self.snap = {}
        self.snap_at = 0.0
        self._next_blink = 3.0
        self._last_footer = -1
        self._last_text = -1
        self._cmd_key = None
        self._cmd_at = 0.0
        self._pen = (0, 0)        # how far the page has been written (line, x)
        self._proj_chars = 38     # header room for the project name

        # ---- header ---------------------------------------------------
        self.project = _label("Claude Code", GREY, 1, (0.0, 0.5), (6, 10))
        self.append(self.project)
        self.dot = Circle(250, 10, 4, fill=GREY)
        self.append(self.dot)
        self.state_word = _label("IDLE", GREY, 1, (0.0, 0.5), (260, 10))
        self.append(self.state_word)
        self.append(Rect(0, 20, 320, 1, fill=0x2A2F36))

        # ---- buddy ----------------------------------------------------
        self.buddy = displayio.Group(x=BX, y=BY)
        self.append(self.buddy)
        self.arm_l = Rect(-12, 36, 14, 10, fill=ORANGE_DK)
        self.arm_r = Rect(98, 36, 14, 10, fill=ORANGE_DK)
        self.buddy.append(self.arm_l)
        self.buddy.append(self.arm_r)
        self.buddy.append(Rect(18, 76, 16, 12, fill=ORANGE_DK))
        self.buddy.append(Rect(66, 76, 16, 12, fill=ORANGE_DK))
        self.band = RoundRect(-4, -12, 108, 44, 20, outline=PHONES, stroke=4)
        self.buddy.append(self.band)
        self.body = RoundRect(0, 0, 100, 80, 18, fill=ORANGE)
        self.buddy.append(self.body)
        self.cups = displayio.Group()
        for x in (-9, 95):
            self.cups.append(RoundRect(x, 8, 14, 28, 5, fill=PHONES))
            self.cups.append(Rect(x + 5, 13, 4, 18, fill=GREEN))
        self.buddy.append(self.cups)
        self.band.hidden = self.cups.hidden = True

        self.eyes_open = displayio.Group()
        self.eyes_open.append(RoundRect(26, 22, 12, 22, 5, fill=INK))
        self.eyes_open.append(RoundRect(62, 22, 12, 22, 5, fill=INK))
        self.eyes_closed = displayio.Group()
        self.eyes_closed.append(Rect(24, 34, 16, 4, fill=INK))
        self.eyes_closed.append(Rect(60, 34, 16, 4, fill=INK))
        self.eyes_happy = displayio.Group()
        for x in (24, 60):
            self.eyes_happy.append(Triangle(x, 40, x + 8, 26, x + 16, 40, fill=INK))
            self.eyes_happy.append(Triangle(x + 4, 41, x + 8, 33, x + 12, 41, fill=ORANGE))
        self.mouth_flat = Rect(42, 56, 16, 4, fill=INK)
        self.mouth_o = Circle(50, 60, 6, fill=INK)
        self.mouth_smile = displayio.Group()
        self.mouth_smile.append(RoundRect(40, 52, 20, 12, 6, fill=INK))
        self.mouth_smile.append(Rect(40, 52, 20, 5, fill=ORANGE))
        self._eyes = {"open": self.eyes_open, "closed": self.eyes_closed,
                      "happy": self.eyes_happy}
        self._mouths = {"flat": self.mouth_flat, "o": self.mouth_o,
                        "smile": self.mouth_smile}
        for part in list(self._eyes.values()) + list(self._mouths.values()):
            part.hidden = True
            self.buddy.append(part)
        self.chin_hand = RoundRect(58, 60, 15, 11, 5, fill=ORANGE_DK)   # thinking
        self.chin_hand.hidden = True
        self.buddy.append(self.chin_hand)

        # ---- props (only one visible at a time) -----------------------
        self.props = {}

        # page: read (marker scans lines) / edit + plan (pencil writes lines)
        g = displayio.Group()
        self.sheet = displayio.Group(x=PAGE_X, y=PAGE_Y)
        self.page_bmp = displayio.Bitmap(46, 58, 3)
        pal = displayio.Palette(3)
        pal[0] = WHITE
        pal[1] = GREY        # printed text (reading)
        pal[2] = 0x3B5BA5    # ink (writing)
        self.sheet.append(displayio.TileGrid(self.page_bmp, pixel_shader=pal))
        self.page_marker = Rect(2, 7, 3, 5, fill=0x2196F3)
        self.sheet.append(self.page_marker)
        g.append(self.sheet)
        self.pencil = _pencil()
        g.append(self.pencil)
        self.props["page"] = g

        # terminal + keyboard: bash (buddy types commands)
        g = displayio.Group()
        term = displayio.Group(x=TERM_X, y=TERM_Y)
        term.append(Rect(0, 0, 100, 56, fill=0x000000, outline=TERM_GREEN))
        self.term_lines = []
        for r in range(TERM_ROWS):
            lb = _label(" ", TERM_GREEN, 1, (0.0, 0.5), (5, 9 + r * 11))
            self.term_lines.append(lb)
            term.append(lb)
        self.term_out = []       # "output" lines that scroll in under the command
        for w in (70, 44, 84, 56):
            ln = Rect(5, 0, w, 3, fill=0x1E9E40)
            self.term_out.append(ln)
            term.append(ln)
        g.append(term)
        g.append(RoundRect(KB_X, KB_Y, 124, 22, 4, fill=0x3A3F48, outline=0x5A606A))
        for x in KEY_XS:
            g.append(Rect(x, KB_Y + 4, 10, 6, fill=0x6B7280))
        g.append(Rect(KB_X + 37, KB_Y + 13, 50, 5, fill=0x6B7280))
        self.key_lit = Rect(0, KB_Y + 4, 10, 6, fill=WHITE)
        g.append(self.key_lit)
        self.hand_l = RoundRect(KB_X + 26, KB_Y - 6, 18, 11, 5, fill=ORANGE_DK)
        self.hand_r = RoundRect(KB_X + 80, KB_Y - 6, 18, 11, 5, fill=ORANGE_DK)
        g.append(self.hand_l)
        g.append(self.hand_r)
        self.props["term"] = g

        # globe: web
        g = displayio.Group(x=236, y=62)
        g.append(Circle(26, 26, 22, outline=0x4DD0E1))
        g.append(RoundRect(14, 4, 25, 45, 12, outline=0x4DD0E1))   # before the lines:
        g.append(Rect(4, 26, 45, 1, fill=0x4DD0E1))                # its middle is black
        g.append(Rect(26, 4, 1, 45, fill=0x4DD0E1))
        self.globe_dot = Circle(0, 0, 3, fill=WHITE)
        g.append(self.globe_dot)
        self.props["globe"] = g

        # thought bubble: thinking
        g = displayio.Group()
        self.puffs = [Circle(214, 46, 3, fill=WHITE), Circle(224, 36, 5, fill=WHITE),
                      RoundRect(232, 22, 66, 30, 12, fill=WHITE)]
        for p in self.puffs:
            g.append(p)
        self.bubble_text = _label("...", INK, 2, (0.5, 0.5), (265, 36))
        g.append(self.bubble_text)
        self.props["bubble"] = g

        # light bulb: pops over the head when thinking turns into doing
        self.bulb = displayio.Group(x=BX + 50, y=30)
        for rx, ry, rw, rh in ((-1, -16, 2, 5), (-15, -3, 5, 2), (10, -3, 5, 2),
                               (-12, -13, 3, 3), (9, -13, 3, 3)):
            self.bulb.append(Rect(rx, ry, rw, rh, fill=YELLOW))
        self.bulb.append(Circle(0, -2, 7, fill=YELLOW))
        self.bulb.append(Rect(-4, 4, 8, 6, fill=GREY))
        self.bulb.hidden = True
        self._bulb_until = 0.0
        self._bulb_pending = False

        # mini buddies: agent
        g = displayio.Group(x=232, y=96)
        self.minis = []
        for i in range(2):
            m = displayio.Group(x=i * 36)
            m.append(RoundRect(0, 0, 24, 18, 6, fill=ORANGE))
            m.append(Rect(6, 5, 3, 6, fill=INK))
            m.append(Rect(15, 5, 3, 6, fill=INK))
            self.minis.append(m)
            g.append(m)
        self.props["minis"] = g

        # spinner: generic tool / compact
        g = displayio.Group()
        self.spin_text = _label("|", 0x90A4AE, 4, (0.5, 0.5), (266, 90))
        g.append(self.spin_text)
        self.props["spinner"] = g

        # alert: permission (!) / waiting (?)
        g = displayio.Group()
        self.sign = displayio.Group(x=240, y=46)          # permission: waved sign
        self.sign.append(Rect(20, 40, 4, 34, fill=0x8D6E63))
        self.sign.append(RoundRect(0, 0, 44, 44, 8, fill=YELLOW, outline=INK, stroke=3))
        self.sign.append(_label("!", INK, 4, (0.5, 0.5), (22, 22)))
        g.append(self.sign)
        self.bursts = []
        for bx, by, bw, bh in ((232, 50, 5, 3), (290, 50, 5, 3), (228, 70, 6, 3),
                               (292, 70, 6, 3), (236, 88, 5, 3), (287, 88, 5, 3)):
            r = Rect(bx, by, bw, bh, fill=YELLOW)
            self.bursts.append(r)
            g.append(r)
        self.alert_text = _label("?", 0xFFF176, 4, (0.5, 0.5), (262, 70))
        g.append(self.alert_text)
        self.props["alert"] = g

        # sparkles: done
        g = displayio.Group()
        self.sparkles = []
        for (x, y) in ((92, 48), (236, 44), (86, 118), (240, 116), (268, 80)):
            s = _label("*", YELLOW, 2, (0.5, 0.5), (x, y))
            self.sparkles.append(s)
            g.append(s)
        self.props["sparkle"] = g

        # zzz: bye
        g = displayio.Group()
        self.zs = []
        for i in range(3):
            z = _label("z", 0x90A4AE, i + 1, (0.5, 0.5), (220, 48))
            self.zs.append(z)
            g.append(z)
        self.props["zzz"] = g

        # music notes: listening
        g = displayio.Group()
        self.notes = []
        for i in range(3):
            n = _note(GREEN if i % 2 == 0 else WHITE)
            self.notes.append(n)
            g.append(n)
        self.props["notes"] = g

        self.props["wave"] = displayio.Group()   # animation only, no prop
        for p in self.props.values():
            p.hidden = True
            self.append(p)

        # ---- status text ----------------------------------------------
        self.verb = _label("Hello!", ORANGE, 3, (0.5, 0.5), (160, 164))
        self.append(self.bulb)
        self.append(self.verb)
        self.detail = _label(" ", WHITE, 2, (0.5, 0.5), (160, 200))
        self.append(self.detail)
        self.footer = _label(" ", DIM, 1, (0.5, 0.5), (160, 230))
        self.append(self.footer)
        self.dots = []
        for _ in range(MAX_DOTS):
            c = Circle(0, DOT_Y, 3, fill=DIM)
            c.hidden = True
            self.dots.append(c)
            self.append(c)

        self._apply_mode("hello")

    # ------------------------------------------------------------------
    def _apply_mode(self, mode):
        verb, color, prop, eyes, mouth, _, _ = MODES[mode]
        if self.mode == "thinking" and mode in BUSY_MODES and mode != "thinking":
            self._bulb_pending = True        # "aha!" - thinking turned into doing
        self.mode = mode
        for name, p in self.props.items():
            p.hidden = name != prop
        for name, e in self._eyes.items():
            e.hidden = name != eyes
        for name, m in self._mouths.items():
            m.hidden = name != mouth
        self.eyes_open.x = 0
        self.eyes_open.y = 0
        self.arm_l.y = 36
        self.arm_r.y = 36
        self.arm_l.hidden = mode == "bash"                       # hands on keyboard
        self.arm_r.hidden = mode in ("bash", "edit", "plan", "thinking")  # pencil / chin
        self.chin_hand.hidden = mode != "thinking"
        self.buddy.x = BX
        self.body.pixel_shader = pal(ORANGE)
        self.verb.color = color
        self.dot.pixel_shader = pal(color)
        self.state_word.color = color
        if mode in ("permission", "waiting"):
            word = "WAIT"
            urgent = mode == "permission"
            self.sign.hidden = not urgent
            self.alert_text.hidden = urgent
            for r in self.bursts:
                r.hidden = True
        elif mode in BUSY_MODES:
            word = "BUSY"
        elif mode == "music":
            word = "MUSIC"
        else:
            word = "IDLE"
        _set_text(self.state_word, word)
        if mode != "music":
            _set_text(self.verb, verb)
        if prop == "page":
            self.sheet.x = PAGE_X
            self.page_marker.hidden = mode != "read"
            self.pencil.hidden = mode == "read"
            self._new_page()
            if mode == "read":
                self._page_write(len(PAGE_W), 0, 1)
        if prop == "spinner":
            self.spin_text.color = color

    def update(self, snap, now):
        """Apply a state snapshot (dict) received from the host bridge."""
        self.snap = snap
        self.snap_at = now
        mode = snap.get("m", "tool")
        if mode not in MODES or mode == "music":
            mode = "tool"
        self.claude_mode = mode
        key = (snap.get("c"), snap.get("n"))
        if key != self._cmd_key:          # a new command: start typing it
            self._cmd_key = key
            self._cmd_at = now
        self._draw_dots(snap.get("sd", ""), snap.get("sf", -1))
        self.music = snap.get("mu") or None
        self.band.hidden = self.cups.hidden = self.music is None
        if not self.music_view and mode != self.mode:
            self._apply_mode(mode)
        self._last_footer = -1
        self._last_text = -1

    def _draw_dots(self, states, focus):
        """Row of dots, one per session, colored by state; the shown one is bigger."""
        if self.music_view:
            states = ""
        n = min(len(states), MAX_DOTS)
        x0 = DOT_RIGHT - (n - 1) * DOT_GAP
        for i, c in enumerate(self.dots):
            c.hidden = i >= n
            if i < n:
                c.x = x0 + i * DOT_GAP
                c.radius = 5 if i == focus else 3
                c.pixel_shader = pal(DOT_COLORS.get(states[i], DIM))
        self.dot.hidden = n > 0
        self._proj_chars = (x0 - 18) // 6 if n else 38

    def set_music_view(self, on):
        """on: Claude is idle but music plays, so show the listening screen."""
        on = bool(on and self.music)
        if on == self.music_view:
            return
        self.music_view = on
        self._draw_dots(self.snap.get("sd", ""), self.snap.get("sf", -1))
        self._apply_mode("music" if on else self.claude_mode)
        self._last_footer = -1
        self._last_text = -1

    def _draw_text(self, now):
        """Header / big / detail text. Long song names scroll."""
        s = self.snap
        mu = self.music
        if self.music_view and mu:
            _set_text(self.project, "Now playing on Spotify")
            self.project.color = GREEN
            _set_text(self.verb, _marquee(mu.get("t", "") or " ", 17, now))
            _set_text(self.detail, _marquee(mu.get("a", "") or " ", 26, now))
            return
        _set_text(self.verb, MODES[self.mode][0])
        _set_text(self.detail, _fit(s.get("d", "") or " ", 26))
        # while music plays, the header alternates project / song
        if mu and int(now / 6) % 2:
            song = mu.get("t", "")
            if mu.get("a"):
                song += " - " + mu["a"]
            _set_text(self.project, "~ " + _marquee(song, self._proj_chars - 2, now))
            self.project.color = GREEN
        else:
            _set_text(self.project, _fit(s.get("p", "") or "Claude Code", self._proj_chars))
            self.project.color = GREY

    # ---- page helpers ----------------------------------------------------
    def _new_page(self):
        self.page_bmp.fill(0)
        self._pen = (0, 0)

    def _page_write(self, line, x, color):
        """Ink the page up to (line, x), drawing only what is new."""
        bmp = self.page_bmp
        l0, x0 = self._pen
        for j in range(l0, min(line, len(PAGE_W) - 1) + 1):
            start = x0 if j == l0 else 0
            end = PAGE_W[j] if j < line else x
            y = 8 + 9 * j
            for px in range(start, end):
                if (px + j * 5) % 9:         # gaps between "words"
                    for dy in range(3):
                        bmp[7 + px, y + dy] = color
        self._pen = (line, x)

    def _page_state(self, now):
        """(line, x) the pencil is at; line == len(PAGE_W) once the page is full."""
        phase = now % PAGE_CYCLE
        if phase >= WRITE_END:
            return len(PAGE_W), 0, phase
        i = int(phase / LINE_T)
        return i, int((phase - i * LINE_T) / LINE_T * PAGE_W[i]), phase

    # ---- terminal helpers ------------------------------------------------
    def _term_state(self, now):
        """(command, seconds since typing started, seconds per key)."""
        c = self.snap.get("c") if not self.music_view else None
        if c:
            cmd = c if len(c) <= MAX_CMD else c[:MAX_CMD - 1] + "~"
            return cmd, now - self._cmd_at, min(KEY_DT, TYPE_TIME / len(cmd))
        idx = int(now / CMD_CYCLE)
        return CMDS[idx % len(CMDS)], now - idx * CMD_CYCLE, KEY_DT

    def pixel_color(self, now):
        """Color for the onboard NeoPixel."""
        if self.mode == "permission":
            return YELLOW if int(now * 4) % 2 else 0x000000
        if self.mode in BUSY_MODES:
            return MODES[self.mode][1]
        return 0x000000

    def _tick_pencil(self, now):
        line, x, phase = self._page_state(now)
        n = len(PAGE_W)
        end_x, end_y = PAGE_X + 7 + PAGE_W[-1], PAGE_Y + 8 + 9 * (n - 1)
        if phase < WRITE_END:                         # writing
            if (line, x) < self._pen:
                self._new_page()
            self.sheet.x = PAGE_X
            self._page_write(line, x, 2)
            px, py = PAGE_X + 7 + x, PAGE_Y + 8 + 9 * line
            py += int(math.sin(now * 40) * 1.5)       # scribble
        elif phase < WRITE_END + PAGE_HOLD:           # done: lift the pencil
            self._page_write(n, 0, 2)
            px, py = end_x, end_y - 6
        elif phase < WRITE_END + PAGE_HOLD + PAGE_SLIDE:   # page slides out
            t = (phase - WRITE_END - PAGE_HOLD) / PAGE_SLIDE
            self.sheet.x = PAGE_X + int(t * 90)
            px, py = end_x, end_y - 6
        else:                                         # fresh page slides in
            if self._pen != (0, 0):
                self._new_page()
            t = (phase - WRITE_END - PAGE_HOLD - PAGE_SLIDE) / PAGE_SLIDE
            self.sheet.x = PAGE_X + 90 - int(t * 90)
            px = end_x + int((PAGE_X + 7 - end_x) * t)
            py = end_y - 6 + int((PAGE_Y + 8 - end_y) * t)
        self.pencil.x = px
        self.pencil.y = py - 21

    # ------------------------------------------------------------------
    def tick(self, now):
        mode = self.mode
        _, _, prop, eyes, _, speed, amp = MODES[mode]

        # bob
        self.buddy.y = BY + int(math.sin(now * speed) * amp)
        if mode == "compact":
            self.buddy.x = BX + (int(now * 30) % 3) - 1
        elif mode == "music":
            self.buddy.x = BX + int(math.sin(now * 2.5) * 6)

        # blink + gaze
        if eyes == "open":
            gx, gy = 0, 0
            if mode == "read":
                gx = 3 + int(math.sin(now * 3) * 3)
                gy = 2
            elif mode in ("edit", "plan"):
                line = min(self._page_state(now)[0], len(PAGE_W) - 1)
                gx, gy = 5, 2 + line                 # follow the pencil down the page
            elif mode == "bash":
                cmd, phase, dt = self._term_state(now)
                if phase < len(cmd) * dt and int(phase * 1.5) % 2 == 0:
                    gx, gy = 2, 6                    # glance at the keyboard
                else:
                    gx, gy = 5, 0                    # watch the screen
            elif mode in ("web", "agent", "tool"):
                gx, gy = 5, 1
            elif mode == "thinking":
                gx, gy = 4 + int(math.sin(now * 0.7) * 3), -6   # pondering, up and away
            elif mode == "permission":
                gx, gy = int(math.sin(now * 9) * 2), 0          # looking right at you
            self.eyes_open.x = gx
            self.eyes_open.y = gy
            blinking = now >= self._next_blink
            if blinking and now >= self._next_blink + 0.15:
                self._next_blink = now + 2.5 + (int(now * 7) % 30) / 10
                blinking = False
            self.eyes_open.hidden = blinking
            self.eyes_closed.hidden = not blinking

        # props
        if prop == "page":
            if mode == "read":
                self.page_marker.y = 7 + (int(now * 3) % 5) * 9
            else:
                self._tick_pencil(now)
        elif prop == "term":
            cmd, phase, dt = self._term_state(now)
            keys = int(phase / dt)
            typing = keys < len(cmd)
            cursor = "_" if typing or int(now * 3) % 2 else ""
            text = "$ " + cmd[:keys] + cursor
            for r, lb in enumerate(self.term_lines):
                _set_text(lb, text[r * TERM_COLS:(r + 1) * TERM_COLS] or " ")
            # once typed, output scrolls in below it (newest at the bottom)
            for ln in self.term_out:
                ln.hidden = True
            t_out = phase - len(cmd) * dt - 0.3
            if t_out >= 0:
                k = int(t_out / 0.35)
                for row in range(min(k + 1, 2)):
                    ln = self.term_out[(k - row) % len(self.term_out)]
                    ln.y = 47 - row * 7
                    ln.hidden = False
            # alternate hands tapping while a command is being typed
            left = keys % 2 == 0
            pressed = typing and (phase % dt) < dt * 0.6
            self.hand_l.y = KB_Y - 6 + (3 if pressed and left else 0)
            self.hand_r.y = KB_Y - 6 + (3 if pressed and not left else 0)
            self.key_lit.hidden = not pressed
            if pressed:
                k = (keys * 5 + ord(cmd[keys])) % 4
                self.key_lit.x = KEY_XS[k if left else 8 - k]
        elif prop == "globe":
            a = now * 3
            self.globe_dot.x = 26 + int(math.cos(a) * 22)     # circle x/y = center
            self.globe_dot.y = 26 + int(math.sin(a) * 9)
        elif prop == "bubble":
            # every 7.5 s: puffs rise, then ... -> ? -> spinning cog
            phase = now % 7.5
            for i, p in enumerate(self.puffs):
                p.hidden = phase < 0.2 * i
            if phase < 0.6:
                _set_text(self.bubble_text, " ")
            elif phase < 3.0:
                self.bubble_text.color = INK
                _set_text(self.bubble_text, "." * (1 + int(now * 2.5) % 3))
            elif phase < 5.0:
                self.bubble_text.color = 0x7E57C2
                _set_text(self.bubble_text, "?" if int(now * 2) % 3 else "??")
            else:
                self.bubble_text.color = 0x5A606A
                _set_text(self.bubble_text, "+" if int(now * 6) % 2 else "x")
            self.chin_hand.y = 60 - (2 if int(now * 2.5) % 2 else 0)   # chin tap
        elif prop == "minis":
            for i, m in enumerate(self.minis):
                m.y = -int(abs(math.sin(now * 5 + i * 1.6)) * 12)
        elif prop == "spinner":
            _set_text(self.spin_text, SPIN[int(now * 8) % 4])
        elif prop == "alert":
            if mode == "permission":
                # hop, flap both arms, wave the sign, flash
                self.buddy.y = BY - int(abs(math.sin(now * 7)) * 8)
                f = math.sin(now * 14)
                self.arm_l.y = 26 + int(f * 12)
                self.arm_r.y = 26 - int(f * 12)
                self.sign.x = 240 + int(math.sin(now * 16) * 4)
                self.sign.y = 46 - int(abs(math.sin(now * 7)) * 4)
                on = int(now * 3) % 2
                self.body.pixel_shader = pal(YELLOW if on else ORANGE)
                for i, r in enumerate(self.bursts):
                    r.hidden = (i + int(now * 6)) % 2 == 0
            else:
                self.alert_text.anchored_position = (262, 70 + int(math.sin(now * 2) * 5))
        elif prop == "sparkle":
            for i, s in enumerate(self.sparkles):
                s.hidden = math.sin(now * 3 + i * 1.9) < 0.2
        elif prop == "zzz":
            for i, z in enumerate(self.zs):
                k = (now * 0.5 + i / 3) % 1.0
                z.anchored_position = (222 + int(k * 40), 50 - int(k * 34))
        elif prop == "notes":
            for i, n in enumerate(self.notes):
                k = (now * 0.45 + i / 3) % 1.0
                n.x = 228 + i * 18 + int(math.sin(now * 3 + i * 2) * 6)
                n.y = 120 - int(k * 90)
                n.hidden = k > 0.92
        elif prop == "wave":
            self.arm_r.y = 20 + int(math.sin(now * 12) * 10)

        # light bulb pop after thinking
        if self._bulb_pending:
            self._bulb_pending = False
            self._bulb_until = now + 0.9
        show = now < self._bulb_until
        self.bulb.hidden = not show
        if show:
            self.bulb.x = self.buddy.x + 50
            rise = min(0.9 - (self._bulb_until - now), 0.3) / 0.3   # pops up, then stays
            self.bulb.y = self.buddy.y - 8 - int(rise * 4)

        # text, 4x a second (song names scroll)
        q = int(now * 4)
        if q != self._last_text:
            self._last_text = q
            self._draw_text(now)

        # footer, once per second
        sec = int(now)
        if sec != self._last_footer:
            self._last_footer = sec
            s = self.snap
            lag = now - self.snap_at
            parts = []
            if self.music_view:
                s = {}
                parts.append("Claude is resting")
            if "n" in s:
                parts.append("%d tools" % s["n"])
            if "el" in s:
                parts.append(_fmt_elapsed(s["el"] + lag))
            if mode in ("done", "waiting", "bye") and "age" in s:
                parts.append("idle " + _fmt_elapsed(s["age"] + lag))
            _set_text(self.footer, "  |  ".join(parts) or " ")
