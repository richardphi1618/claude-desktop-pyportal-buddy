# Weather screen for the PyPortal, drawn from data the host bridge sends over USB.
#
# Adapted from Adafruit's openweather_graphics.py
# (SPDX-FileCopyrightText: 2019 Limor Fried for Adafruit Industries, MIT).
# The device does no networking: host/pyportal_bridge.py fetches OpenWeather and
# sends a compact dict:
#   {"i": "01d", "c": "Richmond, US", "w": "Clear", "d": "Clear sky", "t": 54, "u": "F"}
# Uses the fonts/, icons/ and weather_background.bmp already on CIRCUITPY.
import time
import displayio
from adafruit_display_text.label import Label
from adafruit_bitmap_font import bitmap_font

cwd = ("/" + __file__).rsplit('/', 1)[0]

GLYPHS = b'0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-,.: '


class WeatherView(displayio.Group):
    def __init__(self, *, am_pm=True):
        super().__init__()
        self.am_pm = am_pm
        self._icon_group = displayio.Group()
        self.append(self._icon_group)
        self._text_group = displayio.Group()
        self.append(self._text_group)
        self._icon_name = None
        self._set_icon(cwd + "/weather_background.bmp")
        self.shown = None

        small = bitmap_font.load_font(cwd + "/fonts/Arial-12.bdf")
        medium = bitmap_font.load_font(cwd + "/fonts/Arial-16.bdf")
        large = bitmap_font.load_font(cwd + "/fonts/Arial-Bold-24.bdf")
        for f in (small, medium, large):
            f.load_glyphs(GLYPHS)
        large.load_glyphs(('°',))

        self.time_text = self._label(medium, 200, 12)
        self.city_text = self._label(medium, 10, 12)
        self.temp_text = self._label(large, 200, 195)
        self.main_text = self._label(large, 10, 195)
        self.description_text = self._label(small, 10, 225)
        self.description_text.text = "Waiting for weather from computer"

    def _label(self, font, x, y):
        lbl = Label(font, color=0xFFFFFF)
        lbl.x, lbl.y = x, y
        self._text_group.append(lbl)
        return lbl

    def show(self, wx):
        """Draw a weather dict from the bridge (no-op if it hasn't changed)."""
        if wx == self.shown:
            return
        self.shown = wx
        icon = wx.get("i", "")
        if icon:
            self._set_icon(cwd + "/icons/" + icon + ".bmp")
        self.city_text.text = wx.get("c", "")
        self.main_text.text = wx.get("w", "")
        if "t" in wx:
            self.temp_text.text = "%d °%s" % (wx["t"], wx.get("u", "F"))
        self.description_text.text = wx.get("d", "")
        self.update_time()

    def update_time(self):
        hour, minute = time.localtime()[3:5]
        fmt = "%d:%02d"
        if self.am_pm:
            fmt += " PM" if hour >= 12 else " AM"
            hour = hour % 12 or 12
        self.time_text.text = fmt % (hour, minute)

    def _set_icon(self, filename):
        if filename == self._icon_name:
            return
        try:
            bmp = displayio.OnDiskBitmap(filename)
        except OSError:
            print("weather icon missing:", filename)
            return
        if len(self._icon_group):
            self._icon_group.pop()
        self._icon_group.append(displayio.TileGrid(bmp, pixel_shader=bmp.pixel_shader))
        self._icon_name = filename
