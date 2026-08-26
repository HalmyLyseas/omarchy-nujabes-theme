#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Nujabes screensaver -- a calm, atmospheric terminal screensaver inspired by the
Nujabes theme wallpaper: the spaced title, the katakana, the vinyl, and the
violet-smoke / amber-strand palette over near-black.

Nothing here blinks, snaps or explodes. A record turns, light drifts across the
lettering, dust floats, and wisps of pale smoke peel off the turning record,
drift across the screen, and come back around from the far side. Colors are
read from the *current* Omarchy theme, so it follows a theme switch instead of
hard-coding Nujabes.

Exits on any keypress, on any signal, or when its window loses focus.
"""

import json
import math
import os
import re
import select
import signal
import socket
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
# One purple/gold period per this many columns -- roughly the title's width.
SMOKE_WAVELENGTH = 102.0
SMOKE_K = 2.0 * math.pi / SMOKE_WAVELENGTH
# How fast the bands slide sideways, in radians/sec: a full purple -> gold ->
# purple cycle takes 2*pi/SMOKE_DRIFT seconds. Raise it to speed the cycling up.
SMOKE_DRIFT = 0.50

# Nujabes fallback, used when the theme has no colors.toml. These keys are
# also the parse whitelist in load_palette, so this table is exactly the set
# of theme colors the renderer consumes.
FALLBACK = {
    "background": "#0d0a11",
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
    found = set()
    try:
        with open(THEME_COLORS) as fh:
            for line in fh:
                m = re.match(
                    r'\s*([a-z_]+)\s*=\s*"(#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3}))"',
                    line,
                )
                if m and m.group(1) in vals:
                    vals[m.group(1)] = m.group(2)
                    found.add(m.group(1))
    except OSError:
        pass
    pal = {k: hex_to_rgb(v) for k, v in vals.items()}
    # Several stock themes define no `orange`; letting them inherit Nujabes
    # amber paints a foreign warm tone into their sweep and record (jarring on
    # a greyscale theme like `white`). A theme that speaks for itself but
    # stays silent on orange gets a tone derived from its own colors instead.
    if found and "orange" not in found:
        pal["orange"] = lerp(pal["accent"], pal["bright_foreground"], 0.35)
    return pal


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

    def __init__(self, cols, rows, titles, kana, tagline):
        self.cols, self.rows = cols, rows
        self.blocks = []          # (row, col, [lines], layer)
        self.record = None        # (top, left, height, width) or None

        kw = max((len(l) for l in kana), default=0)
        kh = len(kana)
        tagw = len(tagline)

        # The full-width title art needs a wide window; rather than silently
        # dropping the theme's centerpiece on a laptop panel, fall through the
        # title variants (widest first) to the first one that fits.
        title = next(
            (
                t for t in titles
                if t
                and max(len(l) for l in t) <= cols - 2
                and rows >= len(t) + 4
            ),
            None,
        )
        show_kana = kana and kw <= cols - 2

        pieces = []               # (height, kind, payload)
        if title:
            pieces.append((len(title), "title", title))
        if show_kana:
            # Like the record and tagline, kana is dropped -- not drawn
            # clipped -- when the rows cannot hold it below the title. The
            # gap only separates kana from a title above it; counting it
            # with no title shown sits the whole layout one row low.
            used = sum(p[0] for p in pieces) + (1 if pieces else 0)
            if used + kh <= rows:
                if pieces:
                    pieces.append((1, "gap", None))
                pieces.append((kh, "kana", kana))

        # A record only earns its space on a roomy screen. Width rides along
        # with height from here, so the fit test and the placement below can
        # never disagree about the record's aspect.
        rec = None
        for h in (20, 16, 13, 10):
            w = int(h * 2.15) + 1
            used = sum(p[0] for p in pieces) + 2 + h + 2 + 1
            if used <= rows - 1 and w <= cols - 4:
                rec = (h, w)
                break
        if rec:
            pieces.append((2, "gap2", None))
            pieces.append((rec[0], "record", rec))

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
                rh, rw = payload
                self.record = (y, max(0, (cols - rw) // 2), rh, rw)
            else:
                w = max(len(l) for l in payload)
                self.blocks.append((y, max(0, (cols - w) // 2), payload, kind))
            y += h


# --------------------------------------------------------------------------- #
# renderer
# --------------------------------------------------------------------------- #

# Braille dot bits, indexed [subpixel column][subpixel row].
BRAILLE_BITS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))
ANGN = 1024                       # angle buckets for the per-frame lookups
ANGK = ANGN / (2.0 * math.pi)
# A braille subpixel is about this much wider than it is tall, given a terminal
# cell roughly 1:2. Without it the record comes out an ellipse.
SUBPIXEL_ASPECT = 0.93

# How brightly each block kind takes the smoke sweep.
BLOCK_WEIGHTS = {"title": 1.0, "kana": 0.78, "tag": 0.62}
# Color LUT resolutions. The terminal output is 8-bit truecolor, so these
# steps are already finer than what survives quantization -- and coarser
# color steps mean longer same-color runs for paint()'s SGR dedup.
SMOKE_UQ = 96                     # sweep position steps
SMOKE_KQ = 32                     # brightness steps
VINYL_Q = 48                      # record ramp steps

# The cloud: wisps of smoke particles advected by a potential-flow field --
# uniform wind plus the classic flow-past-a-cylinder deflection at the record
# and a tangential term for its spin. A wisp is emitted at the record's rim,
# parts around the disc on every pass, and wraps particle-by-particle at the
# screen edges (leaving one side is re-entering the other), so the smoke
# circulates instead of dying off-screen. Diffusion would eventually smear a
# wisp into haze, so each lives a couple of laps, fades, and is replaced by a
# fresh puff off the record. Positions are in braille-iso units (a cell is 1
# wide by 2 tall), which is what makes the disc round.
CLOUD_PARTICLES = 160             # per wisp
CLOUD_LEN = 9.0                   # wisp half-length, cells
CLOUD_THICK = 4.0                 # wisp half-height, iso units
CLOUD_WIND = 0.42                 # drift, cells per frame (~6 cells/s)
CLOUD_ENTRAIN = 0.30              # tangential speed at the rim, cells/frame
CLOUD_PAD = 6.0                   # off-screen margin the wrap passes through
CLOUD_LAPS = 2.2                  # wisp lifetime, in screen widths drifted
CLOUD_ALPHA = 0.80                # peak brightness against bright_foreground
CLOUD_Q = 16                      # brightness steps


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
        # frame() and draw_record() color cells from these tables instead of
        # running gradient math per cell per frame. The text LUT is indexed
        # [brightness][sweep position]; the easing curve (**0.62, which holds
        # each color as a band with a quick crossover) is baked into the
        # sweep axis.
        self.smoke_lut = []
        for kq in range(SMOKE_KQ):
            k = kq / (SMOKE_KQ - 1)
            row = []
            for j in range(SMOKE_UQ):
                v = 2.0 * (j / (SMOKE_UQ - 1)) - 1.0
                v = math.copysign(abs(v) ** 0.62, v)
                row.append(dim(ramp(self.smoke, 0.5 + 0.5 * v), k))
            self.smoke_lut.append(row)
        self.vinyl_lut = [
            ramp(self.vinyl, j / (VINYL_Q - 1)) for j in range(VINYL_Q)
        ]
        # Soft gamma so the wisp thins out at its edges instead of stopping.
        self.cloud_lut = [
            dim(p["bright_foreground"],
                CLOUD_ALPHA * (i / (CLOUD_Q - 1)) ** 0.7)
            for i in range(CLOUD_Q)
        ]
        # Widest first; Layout falls through to the first variant that fits.
        self.titles = [load_art("title.txt"), load_art("title-small.txt")]
        self.kana = load_art("kana.txt")
        tag = load_art("tagline.txt")
        self.tagline = tag[0].strip() if tag else ""
        self.stop = threading.Event()
        self.winch = False
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
        # Per-row halves of the smoke wave's tiny y term, for the angle-sum
        # identity in frame().
        self.sin_y = [math.sin(y * 0.025) for y in range(self.rows)]
        self.cos_y = [math.cos(y * 0.025) for y in range(self.rows)]
        self.layout = Layout(
            self.cols, self.rows, self.titles, self.kana, self.tagline
        )
        self.motes = [self._mote(True) for _ in range(max(24, self.cols // 6))]
        self.build_record()
        rec = self.layout.record
        if rec:
            top, left, rh, rw = rec
            # Disc footprint in iso units: rw wide, 2*rh tall -- a circle of
            # radius ~rh, since rw is ~2.15*rh.
            self.cloud_disc = (left + rw / 2.0, (top + rh / 2.0) * 2.0,
                               rh * 1.05)
        else:
            self.cloud_disc = None
        self.cloud_max = 1 if self.cols < 110 else 2
        self.wisps = [self._spawn_wisp()]
        self.cloud_wait = random.randint(100, 250)
        self.dirty = True

    def _spawn_wisp(self):
        """One puff of cloud, peeling off the record's rim."""
        d = random.choice((-1, 1))
        disc = self.cloud_disc
        if disc:
            dcx, dcy, a = disc
            # Just downwind of the rim, off the upper half, as if the spin
            # had shed it. The wisp keeps roughly this height, so each wrap
            # brings it back around the disc.
            x0 = dcx + d * (a * 0.4 + CLOUD_LEN)
            y0 = dcy - a * random.uniform(0.1, 0.7)
        else:
            x0 = random.uniform(0.2, 0.8) * self.cols
            y0 = random.uniform(0.25, 0.75) * self.rows * 2.0
        parts = []
        for _ in range(CLOUD_PARTICLES):
            ox = random.gauss(0.0, CLOUD_LEN)
            oy = random.gauss(0.0, CLOUD_THICK)
            # Denser core, sheer edges.
            al = math.exp(-(ox * ox) / (2.0 * CLOUD_LEN ** 2)
                          - (oy * oy) / (2.0 * CLOUD_THICK ** 2))
            parts.append([x0 + ox, y0 + oy, al])
        life = int(CLOUD_LAPS * (self.cols + 2.0 * CLOUD_PAD) / CLOUD_WIND
                   * random.uniform(0.85, 1.15))
        return {"dir": d, "age": 0, "life": life,
                "ph": random.uniform(0.0, 6.28), "p": parts}

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

    # -- frame ------------------------------------------------------------ #

    def frame(self, t):
        cells = {}          # (row, col) -> (char, rgb)

        # The purple/gold smoke drifting sideways across the lettering.
        # SMOKE_WAVELENGTH is set so one full period spans the title: both
        # colors are on screen at once (NU gold, JAB purple, ES gold) rather
        # than the whole word sitting in one tint. The second, much longer
        # wave only biases the balance so the split never lands in the same
        # place twice. The y term stays tiny on purpose -- one braille cell
        # is four dot rows but only one color, so a real vertical gradient
        # paints the letters in horizontal bands and the strokes read as
        # sliced -- and it is folded in per cell through the angle-sum
        # identity, so all the trig here is per column, not per cell.
        sin_x = [0.0] * self.cols
        cos_x = [0.0] * self.cols
        wave2 = [0.0] * self.cols
        breath = [0.0] * self.cols
        for x in range(self.cols):
            a = x * SMOKE_K - t * SMOKE_DRIFT
            sin_x[x] = math.sin(a)
            cos_x[x] = math.cos(a)
            wave2[x] = 0.12 * math.sin(x * 0.011 + t * 0.30)
            breath[x] = 0.88 + 0.12 * math.sin(t * 0.34 + x * 0.006)
        sin_y, cos_y = self.sin_y, self.cos_y
        lut = self.smoke_lut
        uq1, kq1 = SMOKE_UQ - 1, SMOKE_KQ - 1

        for top, left, lines, kind in self.layout.blocks:
            weight = BLOCK_WEIGHTS.get(kind, 0.8)
            if kind == "tag":
                weight *= 0.80 + 0.20 * math.sin(t * 0.22)
            for dy, line in enumerate(lines):
                y = top + dy
                if not (0 <= y < self.rows):
                    continue
                sy, cy = sin_y[y], cos_y[y]
                for dx, ch in enumerate(line):
                    if ch == " ":
                        continue
                    x = left + dx
                    if 0 <= x < self.cols:
                        # u stays in [0, 1] by construction (0.5 +- 0.38
                        # +- 0.12) and int() truncates the float fuzz at
                        # both ends, so the indexes need no clamping.
                        u = (
                            0.5
                            + 0.38 * (sin_x[x] * cy + cos_x[x] * sy)
                            + wave2[x]
                        )
                        cells[(y, x)] = (
                            ch,
                            lut[int(weight * breath[x] * kq1)][int(u * uq1)],
                        )

        if self.layout.record:
            self.draw_record(cells, t)

        self.draw_cloud(cells, t)
        self.draw_motes(cells, t)
        return cells

    def build_record(self):
        """Precompute the record's subpixel geometry once per terminal size.

        Radius classifies every subpixel here, once: label, rim and groove
        subpixels land in separate lists (hole and label-gap ones are not
        kept at all), so the per-frame loops in draw_record touch nothing
        whose outcome cannot change between frames.
        """
        self.rec_cells = []
        self.label_sub = []       # (cell index, angle bucket, braille bit)
        self.rim_sub = []
        self.groove_sub = []      # ... plus the pre-scaled groove position
        if not self.layout.record:
            return
        top, left, rh, rw = self.layout.record
        sw, sh = rw * 2, rh * 4
        scx, scy = sw / 2.0 - 0.5, sh / 2.0 - 0.5
        R = sh / 2.0 - 1.0
        r_label = R * 0.33
        r_rim = R - 1.4
        r_hole = R * 0.055 + 1.2

        idx = {}
        for ry in range(rh):
            for rx in range(rw):
                for c in range(2):
                    for r_ in range(4):
                        sx, sy = rx * 2 + c, ry * 4 + r_
                        dx = (sx - scx) * SUBPIXEL_ASPECT
                        dy = sy - scy
                        rad = math.hypot(dx, dy)
                        if rad > R + 0.5 or rad < r_hole:
                            continue
                        # A thin unprinted gap rings the label so it reads as
                        # a label instead of more grooves.
                        if r_label - 1.6 <= rad < r_label:
                            continue
                        key = (top + ry, left + rx)
                        ci = idx.get(key)
                        if ci is None:
                            ci = idx[key] = len(self.rec_cells)
                            self.rec_cells.append(key)
                        ai = int(math.atan2(dy, dx) * ANGK)
                        bit = BRAILLE_BITS[c][r_]
                        if rad < r_label:
                            self.label_sub.append((ci, ai, bit))
                        elif rad > r_rim:
                            self.rim_sub.append((ci, ai, bit))
                        else:
                            self.groove_sub.append(
                                (ci, ai, bit, (rad + 1.0) * 50.0)
                            )

        # Averaging weights and reusable buffers, so the per-frame work
        # allocates nothing and never re-counts the (fixed) subpixels.
        n = len(self.rec_cells)
        cnt = [0] * n
        for group in (self.label_sub, self.rim_sub, self.groove_sub):
            for entry in group:
                cnt[entry[0]] += 1
        self.rec_inv = [1.0 / c if c else 0.0 for c in cnt]
        self.rec_bits = [0] * n
        self.rec_acc = [0.0] * n
        self._rec_zero_i = [0] * n
        self._rec_zero_f = [0.0] * n

        # One dominant sheen with a faint opposed one, the way light catches
        # a turning record. Every rotating cue below is deliberately 1-fold
        # (unique around the full turn): near-equal opposed sheens and a
        # 3-lobed warp made the pattern nearly repeat every half or third
        # turn, and the eye latched onto the next arc as it arrived -- the
        # spin read as stalling or reversing.
        self.spec_tab = []
        self.warp50_tab = []
        self.label_tab = []
        for i in range(ANGN):
            p = i / ANGK
            d1 = abs((p + math.pi) % (2 * math.pi) - math.pi)
            d2 = abs(p % (2 * math.pi) - math.pi)
            spec = (
                math.exp(-(d1 / 0.50) ** 2) + 0.40 * math.exp(-(d2 / 0.50) ** 2)
            )
            self.spec_tab.append(spec)
            # A record never sits perfectly on the spindle: a single-lobe
            # warp reads as a slightly eccentric pressing, and that sway
            # sweeping around is the grooves' clockwise cue. Pre-scaled by
            # the groove table's 50 samples per radius unit, so the
            # per-frame groove index is a single add.
            self.warp50_tab.append(0.30 * math.sin(p) * 50.0)
            # The label carries a soft lobe of its own -- light on paper --
            # so the disc's center shows the turn too.
            d3 = abs((p - 1.1 + math.pi) % (2 * math.pi) - math.pi)
            self.label_tab.append(
                0.20 + 0.34 * spec + 0.10 * math.exp(-(d3 / 0.90) ** 2)
            )

        # Groove profile, 50 samples per radius unit (matching the pre-scaled
        # warp above).
        self.groove_tab = [
            (math.sin(i * 0.02 * 0.5 * math.pi) + 1.0) / 2.0
            for i in range(int((R + 4) / 0.02) + 2)
        ]

    def draw_record(self, cells, t):
        if not self.rec_cells:
            return
        theta = t * 0.55                      # a slow, steady turn
        # Quantizing theta to a table bucket before the subtraction differs
        # from exact math by at most one bucket in ANGN -- invisible, and it
        # keeps the per-subpixel angle work to an integer subtract.
        itheta = int(theta * ANGK)
        bits = self.rec_bits
        acc = self.rec_acc
        bits[:] = self._rec_zero_i
        acc[:] = self._rec_zero_f
        spec_tab = self.spec_tab
        warp50 = self.warp50_tab
        groove_tab = self.groove_tab

        label_tab = self.label_tab
        for ci, ai, bit in self.label_sub:    # paper label, with its own lobe
            acc[ci] += label_tab[(ai - itheta) % ANGN]
            bits[ci] |= bit
        for ci, ai, bit in self.rim_sub:
            acc[ci] += 0.26 + 0.46 * spec_tab[(ai - itheta) % ANGN]
            bits[ci] |= bit
        for ci, ai, bit, gpos in self.groove_sub:
            i = (ai - itheta) % ANGN
            g = groove_tab[int(gpos + warp50[i])]
            # Crisp concentric grooves: the ridge itself decides the dot;
            # dithering here would dissolve the rings into noise.
            if g > 0.46:
                bits[ci] |= bit
            acc[ci] += 0.10 + 0.30 * g + 0.62 * spec_tab[i] * (0.30 + 0.70 * g)

        vinyl_lut = self.vinyl_lut
        inv = self.rec_inv
        vq1 = VINYL_Q - 1
        for ci, (y, x) in enumerate(self.rec_cells):
            b = bits[ci]
            if not b or not (0 <= y < self.rows and 0 <= x < self.cols):
                continue
            j = int(acc[ci] * inv[ci] * 1.9 * vq1)
            cells[(y, x)] = (chr(0x2800 + b), vinyl_lut[j if j < vq1 else vq1])

    def draw_cloud(self, cells, t):
        if (not self.wisps or self.cloud_wait <= 0) \
                and len(self.wisps) < self.cloud_max:
            self.wisps.append(self._spawn_wisp())
            self.cloud_wait = random.randint(100, 250)
        self.cloud_wait -= 1
        if not self.wisps:
            return

        disc = self.cloud_disc
        rows, cols = self.rows, self.cols
        span = cols + 2.0 * CLOUD_PAD
        cell_bits = {}
        cell_amp = {}
        gauss = random.gauss
        for w in self.wisps:
            w["age"] += 1
            # Fade in off the rim, fade out at end of life. A fully faded
            # wisp still advects (so its last frames stay coherent) but
            # draws nothing.
            fade = min(1.0, w["age"] / 40.0,
                       max(0.0, (w["life"] - w["age"]) / 80.0))
            draw = fade > 0.0
            U = CLOUD_WIND * w["dir"]
            bob = 0.06 * math.sin(t * 0.35 + w["ph"])
            for part in w["p"]:
                X, Y, al = part
                vx, vy = U, bob
                if disc is not None:
                    dcx, dcy, a = disc
                    xi = X - dcx
                    eta = Y - dcy
                    r2 = xi * xi + eta * eta
                    if r2 < 4.0:
                        r2 = 4.0
                    a2 = a * a
                    if r2 < a2:
                        # Strayed inside the disc footprint (jitter can push
                        # a particle in): ease it straight back out.
                        k = 0.35 * (a - math.sqrt(r2)) / math.sqrt(r2)
                        vx += xi * k
                        vy += eta * k
                    else:
                        # Flow past a cylinder: stagnates upstream, speeds
                        # up over the top and bottom, rejoins downstream...
                        b = a2 / r2
                        vx -= U * b * (xi * xi - eta * eta) / r2
                        vy -= U * b * 2.0 * xi * eta / r2
                        # ...plus entrainment with the record's (clockwise)
                        # spin, strongest at the rim.
                        s = CLOUD_ENTRAIN * b / math.sqrt(r2)
                        vx -= s * eta
                        vy += s * xi
                X += vx + gauss(0.0, 0.05)
                Y += vy + gauss(0.0, 0.05)
                # Leaving one side of the screen is re-entering the other:
                # each particle wraps on its own, so the head of a wisp comes
                # back while its tail is still going out.
                if X < -CLOUD_PAD:
                    X += span
                elif X >= cols + CLOUD_PAD:
                    X -= span
                part[0], part[1] = X, Y
                yc = Y * 0.5
                cx = int(X)
                cy = int(yc)
                if draw and 0 <= cy < rows and 0 <= cx < cols:
                    key = (cy, cx)
                    if key in cells:      # stay behind text and record
                        continue
                    bit = BRAILLE_BITS[int((X - cx) * 2.0)][int((yc - cy) * 4.0)]
                    cell_bits[key] = cell_bits.get(key, 0) | bit
                    v = al * fade
                    if v > cell_amp.get(key, -1.0):
                        cell_amp[key] = v
        self.wisps = [w for w in self.wisps if w["age"] < w["life"]]

        lut = self.cloud_lut
        cq1 = CLOUD_Q - 1
        for key, b in cell_bits.items():
            cells[key] = (chr(0x2800 + b), lut[int(cell_amp[key] * cq1)])

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
        # Row-major order is load-bearing: it lines cells up so the printed
        # character itself advances the cursor onto the next cell (no
        # per-cell cursor move for a run) and so same-color neighbors land
        # back to back for the SGR dedup.
        py = px = -2
        for y, x in sorted(cells):
            new = cells[y, x]
            if prev.get((y, x)) == new:
                continue
            if y != py or x != px + 1:
                out.append("\033[%d;%dH" % (y + 1, x + 1))
            ch, c = new
            if c != last_color:
                out.append("\033[38;2;%d;%d;%dm" % c)
                last_color = c
            out.append(ch)
            py, px = y, x
        for y, x in prev.keys() - cells.keys():
            out.append("\033[%d;%dH " % (y + 1, x + 1))
        if out:
            sys.stdout.write("".join(out))
            sys.stdout.flush()

    # -- lifecycle -------------------------------------------------------- #

    def watch_focus(self):
        """Leave as soon as the screensaver window is no longer the focus."""
        if self.stop.wait(1.0):   # let the launch settle before judging focus
            return
        if not self._watch_socket():
            self._watch_poll()

    def _focus_lost_now(self):
        """One direct focus query; None when the answer is unknown."""
        try:
            r = subprocess.run(
                ["hyprctl", "activewindow", "-j"],
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode != 0:
                return None
            # Match the window class, not the raw JSON: a substring test
            # also scans titles, and any terminal whose title echoes this
            # string would keep the screensaver alive. An empty workspace
            # answers {} -- no class -- and that counts as focus lost too.
            return json.loads(r.stdout).get("class") != "org.omarchy.screensaver"
        except Exception:
            return None

    def _watch_socket(self):
        """Focus watching off Hyprland's event socket.

        The socket pushes every focus change the moment it happens, where the
        poll below costs a subprocess per second for the screensaver's whole
        life -- a steady 1 Hz wakeup on exactly the idle-on-battery workload
        a screensaver runs in. Returns False when the socket is unavailable
        or dies, and the caller falls back to polling.
        """
        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        run_dir = os.environ.get("XDG_RUNTIME_DIR")
        if not sig or not run_dir:
            return False
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(os.path.join(run_dir, "hypr", sig, ".socket2.sock"))
        except OSError:
            return False
        try:
            # The socket only reports changes, so catch a focus lost before
            # the connect with one direct query.
            if self._focus_lost_now():
                self.stop.set()
                return True
            sock.settimeout(1.0)
            buf = b""
            while not self.stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    return False
                if not chunk:
                    return False  # compositor went away; let the poll decide
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.startswith(b"activewindow>>"):
                        continue
                    cls = line[len(b"activewindow>>"):].split(b",", 1)[0]
                    if cls != b"org.omarchy.screensaver":
                        self.stop.set()
                        return True
            return True
        finally:
            sock.close()

    def _watch_poll(self):
        # A hiccup (slow hyprctl, compositor busy) must not retire the
        # watcher: a screensaver that stops noticing focus loss is worse
        # than one that leaves early. So single failures are skipped, and
        # persistent ones fail toward exiting.
        misses = 0
        while True:
            lost = self._focus_lost_now()
            if lost is None:
                misses += 1
                if misses >= 5:
                    self.stop.set()
                    return
            else:
                misses = 0
                if lost:
                    self.stop.set()
                    return
            if self.stop.wait(1.0):
                return

    def run(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
            signal.signal(sig, lambda *_: self.stop.set())
        # Resizing rebuilds the record's geometry lists, which draw_record may
        # be mid-way through indexing -- so the handler only raises a flag and
        # the rebuild happens between frames.
        signal.signal(signal.SIGWINCH, lambda *_: setattr(self, "winch", True))

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
                if self.winch:
                    self.winch = False
                    self.resize()
                start = time.monotonic()
                if self.dirty:
                    sys.stdout.write("\033[2J")
                    prev = {}
                    self.dirty = False
                cells = self.frame(start - t0)
                self.paint(cells, prev)
                prev = cells
                # Any keystroke ends it. Poll even when the frame overran its
                # budget (timeout 0), or slow frames would starve stdin and
                # leave the keyboard unable to dismiss the screensaver.
                budget = period - (time.monotonic() - start)
                r, _, _ = select.select([sys.stdin], [], [], max(budget, 0.0))
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
            # \033]111 resets the OSC-11 background this saver (and the
            # wrapper before it) changed; without it a manual run leaves the
            # working terminal near-black for the rest of the session.
            sys.stdout.write("\033[?25h\033[?1049l\033[0m\033]111\007")
            sys.stdout.flush()
            # The wrapper restores the compositor cursor too, but only if it
            # lives to run its trap; repeating the restore here covers the
            # wrapper being SIGKILLed while the renderer survives.
            for cmd in (
                ["hyprctl", "eval",
                 "hl.config({ cursor = { invisible = false } })"],
                ["hyprctl", "keyword", "cursor:invisible", "false"],
            ):
                try:
                    r = subprocess.run(cmd, capture_output=True, timeout=3)
                    if r.returncode == 0:
                        break
                except Exception:
                    pass


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
