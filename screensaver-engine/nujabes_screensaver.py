#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Nujabes screensaver -- a calm, atmospheric terminal screensaver inspired by the
Nujabes theme wallpaper: the spaced title, the katakana, the vinyl, and the
violet-smoke / amber-strand palette over near-black.

Nothing here blinks, snaps or explodes. A record turns, light drifts across the
lettering, dust floats. Colors are read from the *current* Omarchy theme, so it
follows a theme switch instead of hard-coding Nujabes.

Exits on any keypress, on any signal, or when its window loses focus.
"""

import math
import os
import re
import select
import signal
import struct
import sys
import termios
import time
import tty
import fcntl
import random
import subprocess
import threading

# The active theme, as omarchy-theme-set leaves it: it copies the whole theme
# directory here, so a theme can carry its own screensaver artwork alongside
# its palette. No art dir means this theme has no screensaver of its own.
THEME = os.path.expanduser("~/.local/state/omarchy/current/theme")
ART = os.path.join(THEME, "screensaver")
THEME_COLORS = os.path.join(THEME, "colors.toml")

FPS = 14.0
DEFAULT_TAGLINE = ""
# One purple/gold period per this many columns -- roughly the title's width.
SMOKE_WAVELENGTH = 102.0
SMOKE_K = 2.0 * math.pi / SMOKE_WAVELENGTH
# How fast the bands slide sideways, in radians/sec: a full purple -> gold ->
# purple cycle takes 2*pi/SMOKE_DRIFT seconds. Raise it to speed the cycling up.
SMOKE_DRIFT = 0.50

# Nujabes fallback, used when the theme has no colors.toml
FALLBACK = {
    "background": "#0d0a11",
    "foreground": "#e3dcda",
    "bright_foreground": "#fdf7f7",
    "accent": "#b26ac6",
    "orange": "#e88b2c",
    "muted": "#6b5268",
    "selection": "#3d2456",
}


# --------------------------------------------------------------------------- #
# palette
# --------------------------------------------------------------------------- #

def hex_to_rgb(s):
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def load_palette():
    vals = dict(FALLBACK)
    try:
        with open(THEME_COLORS) as fh:
            for line in fh:
                m = re.match(r'\s*([a-z_]+)\s*=\s*"(#[0-9a-fA-F]{3,6})"', line)
                if m and m.group(1) in vals:
                    vals[m.group(1)] = m.group(2)
    except OSError:
        pass
    return {k: hex_to_rgb(v) for k, v in vals.items()}


def lerp(a, b, t):
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def ramp(stops, t):
    """Sample a multi-stop gradient at t in [0,1]."""
    n = len(stops) - 1
    t = 0.0 if t < 0.0 else (0.999999 if t >= 1.0 else t)
    i = int(t * n)
    return lerp(stops[i], stops[i + 1], t * n - i)


def dim(c, k):
    return (int(c[0] * k), int(c[1] * k), int(c[2] * k))


# --------------------------------------------------------------------------- #
# art
# --------------------------------------------------------------------------- #

def load_art(name):
    try:
        with open(os.path.join(ART, name), encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except OSError:
        return []
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


class Layout:
    """Places the pieces for a given terminal size. Degrades on small screens."""

    def __init__(self, cols, rows, title, kana, tagline):
        self.cols, self.rows = cols, rows
        self.tagline = tagline
        self.blocks = []          # (row, col, [lines], layer)
        self.record = None        # (top, left, radius) or None

        tw = max((len(l) for l in title), default=0)
        kw = max((len(l) for l in kana), default=0)
        th, kh = len(title), len(kana)
        tagw = len(tagline)

        show_title = title and tw <= cols - 2 and rows >= th + 4
        show_kana = kana and kw <= cols - 2

        pieces = []               # (height, kind, payload)
        if show_title:
            pieces.append((th, "title", title))
        if show_kana:
            pieces.append((1, "gap", None))
            pieces.append((kh, "kana", kana))

        # A record only earns its space on a roomy screen.
        rec = 0
        for h in (20, 16, 13, 10):
            w = int(h * 2.15) + 1
            used = sum(p[0] for p in pieces) + 2 + h + 2 + 1
            if used <= rows - 1 and w <= cols - 4:
                rec = h
                break
        if rec:
            pieces.append((2, "gap2", None))
            pieces.append((rec, "record", rec))

        if tagline and tagw <= cols - 2 and sum(p[0] for p in pieces) + 3 <= rows:
            pieces.append((2, "gap2", None))
            pieces.append((1, "tag", [tagline]))

        total = sum(p[0] for p in pieces)
        y = max(0, (rows - total) // 2)
        for h, kind, payload in pieces:
            if kind.startswith("gap"):
                y += h
                continue
            if kind == "record":
                w = int(payload * 2.15) + 1
                self.record = (y, max(0, (cols - w) // 2), payload, w)
            else:
                w = max(len(l) for l in payload)
                self.blocks.append((y, max(0, (cols - w) // 2), payload, kind))
            y += h


# --------------------------------------------------------------------------- #
# renderer
# --------------------------------------------------------------------------- #

# Braille dot bits, indexed [subpixel column][subpixel row].
BRAILLE_BITS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))
# 4x4 ordered dither, so groove density reads as shading rather than banding.
BAYER = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)
ANGN = 1024                       # angle buckets for the per-frame lookups
ANGK = ANGN / (2.0 * math.pi)
# A braille subpixel is about this much wider than it is tall, given a terminal
# cell roughly 1:2. Without it the record comes out an ellipse.
SUBPIXEL_ASPECT = 0.93


class Screensaver:
    def __init__(self):
        self.pal = load_palette()
        p = self.pal
        # Text sweeps between the theme's accent and its warm tone only --
        # purple to gold here. The old ramp began on `selection` (a near-black
        # violet) and ended on bone, so the sweep took the lettering down to
        # almost nothing at the extremes. The lifted midpoint keeps the
        # crossover luminous instead of muddy.
        self.smoke = [
            p["accent"],
            lerp(lerp(p["accent"], p["orange"], 0.5), (255, 255, 255), 0.22),
            p["orange"],
        ]
        self.vinyl = [
            dim(p["selection"], 0.7),
            p["muted"],
            p["accent"],
            p["orange"],
            p["bright_foreground"],
        ]
        self.title = load_art("title.txt")
        self.kana = load_art("kana.txt")
        tag = load_art("tagline.txt")
        self.tagline = tag[0].strip() if tag else DEFAULT_TAGLINE
        self.stop = threading.Event()
        self.motes = []
        self.resize()

    def resize(self, *_):
        try:
            data = fcntl.ioctl(sys.stdout, termios.TIOCGWINSZ, b"\0" * 8)
            rows, cols = struct.unpack("HHHH", data)[:2]
        except OSError:
            rows, cols = 24, 80
        self.rows = rows or 24
        self.cols = cols or 80
        self.layout = Layout(
            self.cols, self.rows, self.title, self.kana, self.tagline
        )
        self.motes = [self._mote(True) for _ in range(max(24, self.cols // 6))]
        self.build_record()
        self.dirty = True

    def _mote(self, spread=False):
        return {
            "x": random.uniform(0, self.cols),
            "y": random.uniform(0, self.rows) if spread else self.rows + 1.0,
            "vx": random.uniform(-0.09, 0.16),
            "vy": -random.uniform(0.035, 0.13),
            "a": random.uniform(0.12, 0.5),
            "ch": random.choice("··˙.'"),
            "ph": random.uniform(0, 6.28),
        }

    # -- per-frame color fields ------------------------------------------- #

    def smoke_color(self, x, y, t, weight):
        """Purple/gold drifting sideways across the lettering."""
        # SMOKE_WAVELENGTH is set so one full period spans the title: both
        # colors are on screen at once (NU gold, JAB purple, ES gold) rather
        # than the whole word sitting in one tint. The second, much longer wave
        # only biases the balance so the split never lands in the same place
        # twice. The y term stays tiny on purpose -- one braille cell is four
        # dot rows but only one color, so a real vertical gradient paints the
        # letters in horizontal bands and the strokes read as sliced.
        u = (
            0.5
            + 0.38 * math.sin(x * SMOKE_K - t * SMOKE_DRIFT + y * 0.025)
            + 0.12 * math.sin(x * 0.011 + t * 0.30)
        )
        # Ease toward the ends of the ramp, so each color holds as a band and
        # the crossover between them is comparatively quick.
        v = 2.0 * u - 1.0
        v = math.copysign(abs(v) ** 0.62, v)
        breath = 0.88 + 0.12 * math.sin(t * 0.34 + x * 0.006)
        return dim(ramp(self.smoke, 0.5 + 0.5 * v), weight * breath)

    # -- frame ------------------------------------------------------------ #

    def frame(self, t):
        cells = {}          # (row, col) -> (char, rgb)

        for top, left, lines, kind in self.layout.blocks:
            weight = {"title": 1.0, "kana": 0.78, "tag": 0.62}.get(kind, 0.8)
            if kind == "tag":
                weight *= 0.80 + 0.20 * math.sin(t * 0.22)
            for dy, line in enumerate(lines):
                y = top + dy
                if not (0 <= y < self.rows):
                    continue
                for dx, ch in enumerate(line):
                    if ch == " ":
                        continue
                    x = left + dx
                    if 0 <= x < self.cols:
                        cells[(y, x)] = (ch, self.smoke_color(x, y, t, weight))

        if self.layout.record:
            self.draw_record(cells, t)

        self.draw_motes(cells, t)
        return cells

    def build_record(self):
        """Precompute the record's subpixel geometry once per terminal size.

        Every subpixel's radius and angle are fixed; only the rotation shifts,
        so the per-frame work reduces to two table lookups per subpixel.
        """
        self.rec_sub = []
        self.rec_cells = []
        if not self.layout.record:
            return
        top, left, rh, rw = self.layout.record
        sw, sh = rw * 2, rh * 4
        scx, scy = sw / 2.0 - 0.5, sh / 2.0 - 0.5
        R = sh / 2.0 - 1.0
        self.rec_R = R

        idx = {}
        for ry in range(rh):
            for rx in range(rw):
                for c in range(2):
                    for r_ in range(4):
                        sx, sy = rx * 2 + c, ry * 4 + r_
                        dx = (sx - scx) * SUBPIXEL_ASPECT
                        dy = sy - scy
                        rad = math.hypot(dx, dy)
                        if rad > R + 0.5:
                            continue
                        key = (top + ry, left + rx)
                        ci = idx.get(key)
                        if ci is None:
                            ci = idx[key] = len(self.rec_cells)
                            self.rec_cells.append(key)
                        self.rec_sub.append((
                            ci,
                            rad,
                            math.atan2(dy, dx),
                            BRAILLE_BITS[c][r_],
                            (BAYER[sy & 3][sx & 3] + 0.5) / 16.0,
                        ))

        # Two narrow opposed sheens, the way light catches a turning record.
        self.spec_tab = []
        self.warp_tab = []
        for i in range(ANGN):
            p = i / ANGK
            d1 = abs((p + math.pi) % (2 * math.pi) - math.pi)
            d2 = abs((p) % (2 * math.pi) - math.pi)
            self.spec_tab.append(
                math.exp(-(d1 / 0.50) ** 2) + 0.75 * math.exp(-(d2 / 0.50) ** 2)
            )
            self.warp_tab.append(0.28 * math.sin(3.0 * p))

        # Groove profile, sampled finely enough that the dither does the rest.
        self.groove_tab = [
            (math.sin(i * 0.02 * 0.5 * math.pi) + 1.0) / 2.0
            for i in range(int((R + 4) / 0.02) + 2)
        ]

    def draw_record(self, cells, t):
        if not self.rec_sub:
            return
        theta = t * 0.55                      # a slow, steady turn
        R = self.rec_R
        r_label = R * 0.33
        r_rim = R - 1.4
        r_hole = R * 0.055 + 1.2
        n = len(self.rec_cells)
        bits = [0] * n
        acc = [0.0] * n
        cnt = [0] * n
        spec_tab, warp_tab, groove_tab = self.spec_tab, self.warp_tab, self.groove_tab

        for ci, rad, ang, bit, dth in self.rec_sub:
            i = int((ang - theta) * ANGK) % ANGN
            spec = spec_tab[i]
            if rad < r_hole:
                continue
            if rad < r_label:
                # Paper label: a flat disc, with a thin unprinted gap ringing it
                # so it reads as a label instead of more grooves.
                if rad > r_label - 1.6:
                    continue
                v = 0.20 + 0.34 * spec
                on = True
            elif rad > r_rim:
                v = 0.26 + 0.46 * spec
                on = True
            else:
                g = groove_tab[int((rad + warp_tab[i] + 1.0) * 50.0)]
                # Crisp concentric grooves: dithering here dissolves the rings
                # into noise, so the ridge itself decides the dot.
                on = g > 0.46
                v = 0.10 + 0.30 * g + 0.62 * spec * (0.30 + 0.70 * g)
            acc[ci] += v
            cnt[ci] += 1
            if on:
                bits[ci] |= bit

        vinyl = self.vinyl
        for ci, (y, x) in enumerate(self.rec_cells):
            if not bits[ci] or not (0 <= y < self.rows and 0 <= x < self.cols):
                continue
            v = acc[ci] / (cnt[ci] or 1)
            cells[(y, x)] = (chr(0x2800 + bits[ci]), ramp(vinyl, v * 1.9))

    def draw_motes(self, cells, t):
        for m in self.motes:
            m["x"] += m["vx"] * 0.35
            m["y"] += m["vy"] * 0.35
            if m["y"] < -1 or m["x"] < -1 or m["x"] > self.cols + 1:
                m.update(self._mote())
            y, x = int(m["y"]), int(m["x"])
            if 0 <= y < self.rows and 0 <= x < self.cols and (y, x) not in cells:
                a = m["a"] * (0.45 + 0.55 * (0.5 + 0.5 * math.sin(t * 0.7 + m["ph"])))
                cells[(y, x)] = (m["ch"], dim(self.pal["accent"], a * 0.55))

    # -- paint ------------------------------------------------------------ #

    def paint(self, cells, prev):
        out = []
        last_color = None
        touched = set(cells) | set(prev)
        for y, x in sorted(touched):
            new = cells.get((y, x))
            old = prev.get((y, x))
            if new == old:
                continue
            out.append("\033[%d;%dH" % (y + 1, x + 1))
            if new is None:
                out.append(" ")
                last_color = None
                continue
            ch, c = new
            if c != last_color:
                out.append("\033[38;2;%d;%d;%dm" % c)
                last_color = c
            out.append(ch)
        if out:
            sys.stdout.write("".join(out))
            sys.stdout.flush()

    # -- lifecycle -------------------------------------------------------- #

    def watch_focus(self):
        """Leave as soon as the screensaver window is no longer the focus."""
        while not self.stop.wait(1.0):
            try:
                r = subprocess.run(
                    ["hyprctl", "activewindow", "-j"],
                    capture_output=True, text=True, timeout=3,
                )
                if r.returncode == 0 and "org.omarchy.screensaver" not in r.stdout:
                    self.stop.set()
                    return
            except Exception:
                return

    def run(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
            signal.signal(sig, lambda *_: self.stop.set())
        signal.signal(signal.SIGWINCH, lambda *_: self.resize())

        threading.Thread(target=self.watch_focus, daemon=True).start()

        bg = "#%02x%02x%02x" % self.pal["background"]
        sys.stdout.write(
            "\033]11;%s\007\033[?1049h\033[?25l\033[2J" % bg
        )
        sys.stdout.flush()

        prev = {}
        t0 = time.monotonic()
        period = 1.0 / FPS
        try:
            while not self.stop.is_set():
                start = time.monotonic()
                if self.dirty:
                    sys.stdout.write("\033[2J")
                    prev = {}
                    self.dirty = False
                cells = self.frame(start - t0)
                self.paint(cells, prev)
                prev = cells
                # any keystroke ends it
                budget = period - (time.monotonic() - start)
                if budget > 0:
                    r, _, _ = select.select([sys.stdin], [], [], budget)
                    if r:
                        # A keystroke dismisses it; an empty read means stdin is
                        # gone, and a screensaver nobody can dismiss is worse.
                        try:
                            os.read(sys.stdin.fileno(), 64)
                        except OSError:
                            pass
                        break
        finally:
            self.stop.set()
            sys.stdout.write("\033[?25h\033[?1049l\033[0m")
            sys.stdout.flush()


def main():
    fd = sys.stdin.fileno()
    saved = None
    try:
        saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    except termios.error:
        pass
    try:
        Screensaver().run()
    finally:
        if saved is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)


if __name__ == "__main__":
    main()
