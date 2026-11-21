"""Render the buddy's animations to GIFs, without a PyPortal.

Runs the real device/claude_buddy.py on desktop Python. A small stand-in for
CircuitPython's displayio, vectorio, terminalio, adafruit_display_text and
adafruit_display_shapes draws each frame with Pillow.

    python3 -m venv .venv && .venv/bin/pip install pillow
    .venv/bin/python tools/render_gifs.py            # all scenes
    .venv/bin/python tools/render_gifs.py bash edit  # just some

Writes docs/animations/<scene>.gif and docs/animations/README.md.
The text font is an approximation of the device's built-in terminalio font.
"""
import os
import sys
import types

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "animations")
W, H = 320, 240
FPS = 20
ZOOM = 2                     # output pixels per device pixel
BG = (0, 0, 0)
FONT_FILE = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
GLYPH_W, GLYPH_H = 6, 12     # terminalio cell size


def rgb(c):
    return ((c >> 16) & 255, (c >> 8) & 255, c & 255)


# ---- stand-ins for the CircuitPython display libraries ---------------------
class Group:
    def __init__(self, x=0, y=0, scale=1):
        self.x, self.y, self.hidden = x, y, False
        self._items = []

    def append(self, item):
        self._items.append(item)

    def insert(self, i, item):
        self._items.insert(i, item)

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def draw(self, d, img, ox, oy):
        if self.hidden:
            return
        for it in self._items:
            it.draw(d, img, ox + self.x, oy + self.y)


class Bitmap:
    def __init__(self, w, h, colors):
        self.width, self.height = w, h
        self.px = bytearray(w * h)

    def __setitem__(self, xy, v):
        x, y = xy
        self.px[y * self.width + x] = v

    def __getitem__(self, xy):
        x, y = xy
        return self.px[y * self.width + x]

    def fill(self, v):
        self.px = bytearray([v]) * (self.width * self.height)


class Palette(list):
    def __init__(self, n):
        super().__init__([0] * n)


class TileGrid:
    def __init__(self, bitmap, pixel_shader, x=0, y=0):
        self.bitmap, self.pal, self.x, self.y, self.hidden = bitmap, pixel_shader, x, y, False

    def draw(self, d, img, ox, oy):
        if self.hidden:
            return
        b = self.bitmap
        im = Image.frombytes("P", (b.width, b.height), bytes(b.px))
        flat = []
        for c in self.pal:
            flat += rgb(c)
        im.putpalette(flat)
        img.paste(im.convert("RGB"), (ox + self.x, oy + self.y))


class _Shape:
    """Common bits: x/y are the top-left corner; fill/outline settable."""

    def __init__(self, x, y, fill=None, outline=None, stroke=1):
        self.x, self.y, self.fill, self.outline, self.stroke = x, y, fill, outline, stroke
        self.hidden = False

    def _colors(self):
        return (rgb(self.fill) if self.fill is not None else None,
                rgb(self.outline) if self.outline is not None else None)


class Rect(_Shape):
    def __init__(self, x, y, width, height, *, fill=None, outline=None, stroke=1):
        super().__init__(x, y, fill, outline, stroke)
        self.width, self.height = width, height

    def draw(self, d, img, ox, oy):
        if self.hidden:
            return
        f, o = self._colors()
        x, y = ox + self.x, oy + self.y
        d.rectangle([x, y, x + self.width - 1, y + self.height - 1], fill=f,
                    outline=o, width=self.stroke if o else 0)


class RoundRect(Rect):
    def __init__(self, x, y, width, height, r, *, fill=None, outline=None, stroke=1):
        super().__init__(x, y, width, height, fill=fill, outline=outline, stroke=stroke)
        self.r = r

    def draw(self, d, img, ox, oy):
        if self.hidden:
            return
        f, o = self._colors()
        x, y = ox + self.x, oy + self.y
        d.rounded_rectangle([x, y, x + self.width - 1, y + self.height - 1], radius=self.r,
                            fill=f, outline=o, width=self.stroke if o else 0)


class Circle(RoundRect):
    def __init__(self, x0, y0, r, *, fill=None, outline=None, stroke=1):
        super().__init__(x0 - r, y0 - r, 2 * r + 1, 2 * r + 1, r, fill=fill,
                         outline=outline, stroke=stroke)

    def draw(self, d, img, ox, oy):
        if self.hidden:
            return
        f, o = self._colors()
        x, y = ox + self.x, oy + self.y
        d.ellipse([x, y, x + self.width - 1, y + self.height - 1], fill=f, outline=o,
                  width=self.stroke if o else 0)


class Triangle(_Shape):
    def __init__(self, x0, y0, x1, y1, x2, y2, *, fill=None, outline=None):
        x, y = min(x0, x1, x2), min(y0, y1, y2)
        super().__init__(x, y, fill, outline)
        self.pts = [(x0 - x, y0 - y), (x1 - x, y1 - y), (x2 - x, y2 - y)]

    def draw(self, d, img, ox, oy):
        if self.hidden:
            return
        f, o = self._colors()
        d.polygon([(ox + self.x + px, oy + self.y + py) for px, py in self.pts],
                  fill=f, outline=o)


class _Vector:
    """vectorio shapes: color comes from pixel_shader[0]."""

    def __init__(self, pixel_shader, x=0, y=0):
        self.pixel_shader, self.x, self.y, self.hidden = pixel_shader, x, y, False

    def _c(self):
        return rgb(self.pixel_shader[0])


class VRectangle(_Vector):
    def __init__(self, *, pixel_shader, width, height, x=0, y=0):
        super().__init__(pixel_shader, x, y)
        self.width, self.height = width, height

    def draw(self, d, img, ox, oy):
        if not self.hidden:
            x, y = ox + self.x, oy + self.y
            d.rectangle([x, y, x + self.width - 1, y + self.height - 1], fill=self._c())


class VCircle(_Vector):
    def __init__(self, *, pixel_shader, radius, x=0, y=0):
        super().__init__(pixel_shader, x, y)
        self.radius = radius

    def draw(self, d, img, ox, oy):
        if not self.hidden:
            x, y, r = ox + self.x, oy + self.y, self.radius
            d.ellipse([x - r, y - r, x + r, y + r], fill=self._c())


class VPolygon(_Vector):
    def __init__(self, *, pixel_shader, points, x=0, y=0):
        super().__init__(pixel_shader, x, y)
        self.points = list(points)

    def draw(self, d, img, ox, oy):
        if not self.hidden:
            d.polygon([(ox + self.x + px, oy + self.y + py) for px, py in self.points],
                      fill=self._c())


_FONT = ImageFont.truetype(FONT_FILE, 10)
_glyph_cache = {}


def _text_image(text, color):
    key = (text, color)
    im = _glyph_cache.get(key)
    if im is None:
        im = Image.new("RGBA", (max(len(text), 1) * GLYPH_W, GLYPH_H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.fontmode = "1"                       # no anti-aliasing, like the device
        for i, ch in enumerate(text):
            d.text((i * GLYPH_W, 0), ch, font=_FONT, fill=rgb(color) + (255,))
        _glyph_cache[key] = im
    return im


class Label:
    def __init__(self, font, text="", color=0xFFFFFF, scale=1):
        self.text, self.color, self.scale = text, color, scale
        self.anchor_point = (0.0, 0.0)
        self.anchored_position = (0, 0)
        self.hidden = False

    def draw(self, d, img, ox, oy):
        if self.hidden or not self.text:
            return
        im = _text_image(self.text, self.color)
        if self.scale != 1:
            im = im.resize((im.width * self.scale, im.height * self.scale), Image.NEAREST)
        ax, ay = self.anchor_point
        px, py = self.anchored_position
        x = int(ox + px - ax * im.width)
        y = int(oy + py - ay * im.height)
        img.paste(im, (x, y), im)


def install_fakes():
    def mod(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    mod("displayio", Group=Group, Bitmap=Bitmap, Palette=Palette, TileGrid=TileGrid)
    mod("terminalio", FONT=None)
    mod("adafruit_display_text")
    mod("adafruit_display_text.label", Label=Label)
    mod("adafruit_display_text.bitmap_label", Label=Label)
    mod("vectorio", Rectangle=VRectangle, Circle=VCircle, Polygon=VPolygon)
    mod("adafruit_display_shapes")
    mod("adafruit_display_shapes.rect", Rect=Rect)
    mod("adafruit_display_shapes.roundrect", RoundRect=RoundRect)
    mod("adafruit_display_shapes.circle", Circle=Circle)
    mod("adafruit_display_shapes.triangle", Triangle=Triangle)


# ---- scenes ----------------------------------------------------------------
PROJ = "my-project"
SONG = {"t": "Midnight Compiler (Extended Mix)", "a": "The Semicolons"}


def snap(m, d="", **kw):
    s = {"m": m, "d": d, "p": PROJ, "n": 12, "el": 754, "age": 0, "ss": 1}
    s.update(kw)
    return s


# name: (title, seconds, [(at_seconds, snapshot)], music_view)
SCENES = {
    "hello": ("Hello: session start", 4, [(0, snap("hello", "ready when you are", n=0, el=3))], False),
    "thinking": ("Thinking, then the light bulb as it starts a tool", 9.2,
                 [(0, snap("thinking", "on it...")),
                  (7.6, snap("read", "Read claude_buddy.py"))], False),
    "read": ("Reading: Read / Grep / Glob", 4, [(0, snap("read", "Read claude_buddy.py"))], False),
    "edit": ("Editing: Edit / Write", 6.7, [(0, snap("edit", "Edit code.py"))], False),
    "plan": ("Planning: TodoWrite", 3.4, [(0, snap("plan", "TodoWrite"))], False),
    "bash": ("Running: Bash, typing the real command", 5,
             [(0, snap("bash", "Bash Show working tree status", c="git status --short"))], False),
    "bash_long": ("Running: a long command wraps", 6,
                  [(0, snap("bash", "Bash Run the display tests",
                            c="python3 -m pytest tests/test_display.py -q -x"))], False),
    "web": ("Browsing: WebFetch / WebSearch", 4, [(0, snap("web", "WebSearch displayio docs"))], False),
    "agent": ("Delegating: Agent", 4, [(0, snap("agent", "Agent Find the config files"))], False),
    "tool": ("Using a tool: anything else", 3, [(0, snap("tool", "create_issue"))], False),
    "permission": ("Needs you!: permission prompt", 4,
                   [(0, snap("permission", "OK? Bash rm -rf build"))], False),
    "waiting": ("Your turn: idle notification", 4,
                [(0, snap("waiting", "Claude is waiting for you", age=60))], False),
    "done": ("All done: Stop", 4, [(0, snap("done", "12 tools used", age=2))], False),
    "compact": ("Compacting: PreCompact", 3, [(0, snap("compact", "tidying up context"))], False),
    "bye": ("Goodnight: session end", 5, [(0, snap("bye", "session closed"))], False),
    "music": ("Listening: Spotify playing, Claude idle", 8,
              [(0, snap("bye", age=99999, mu=SONG))], True),
    "sessions": ("Three sessions: dots show each one, the big dot is on screen", 12,
                 [(0, snap("read", "Read routes.py", p="api-server", sd="bb", sf=0, ss=2)),
                  (3, snap("bash", "Bash Run the tests", c="npm test", p="web-ui",
                           sd="bb", sf=1, ss=2)),
                  (6, snap("permission", "OK? Bash git push", p="infrastructure-terraform-modules",
                           sd="bbp", sf=2, ss=3)),
                  (9, snap("done", "31 tools used", p="web-ui", age=1,
                           sd="bdb", sf=1, ss=3))], False),
    "tap": ("Tapped to another session: manual for 30 s, yellow dot still waiting", 5,
            [(0, snap("edit", "Edit routes.py", p="api-server", sd="bpb", sf=0, ss=3, ov=30))],
            False),
    "music_working": ("Headphones on while Claude works", 7,
                      [(0, snap("bash", "Bash Run the tests", c="npm test", mu=SONG))], False),
}


def render(name, buddy_cls):
    title, seconds, events, music_view = SCENES[name]
    buddy = buddy_cls()
    frames = []
    events = list(events)
    n = int(seconds * FPS)
    for i in range(n):
        now = i / FPS
        while events and events[0][0] <= now:
            buddy.update(events.pop(0)[1], now)
        buddy.set_music_view(music_view)
        buddy.tick(now)
        img = Image.new("RGB", (W, H), BG)
        buddy.draw(ImageDraw.Draw(img), img, 0, 0)
        if ZOOM != 1:
            img = img.resize((W * ZOOM, H * ZOOM), Image.NEAREST)
        frames.append(img.quantize(colors=64, method=Image.Quantize.MEDIANCUT,
                                   dither=Image.Dither.NONE))
    path = os.path.join(OUT, name + ".gif")
    frames[0].save(path, save_all=True, append_images=frames[1:], loop=0,
                   duration=int(1000 / FPS), optimize=True, disposal=1)
    print("%-14s %3d frames  %4d KB  %s" % (name, n, os.path.getsize(path) // 1024, title))


def write_index():
    lines = ["# Buddy animations", "",
             "Rendered from `device/claude_buddy.py` by `tools/render_gifs.py` "
             "(regenerate after changing animations). The font is an approximation "
             "of the PyPortal's built-in one.", "",
             "| | |", "|---|---|"]
    names = list(SCENES)
    for a, b in zip(names[0::2], names[1::2] + [None]):
        row = []
        for n in (a, b):
            row.append("**%s**<br>![%s](%s.gif)" % (SCENES[n][0], n, n) if n else "")
        lines.append("| " + " | ".join(row) + " |")
    with open(os.path.join(OUT, "README.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    install_fakes()
    sys.path.insert(0, os.path.join(ROOT, "device"))
    from claude_buddy import ClaudeBuddy  # pylint: disable=import-outside-toplevel
    os.makedirs(OUT, exist_ok=True)
    wanted = sys.argv[1:] or list(SCENES)
    for name in wanted:
        if name not in SCENES:
            sys.exit("unknown scene %r; choose from: %s" % (name, " ".join(SCENES)))
        render(name, ClaudeBuddy)
    write_index()


if __name__ == "__main__":
    main()
