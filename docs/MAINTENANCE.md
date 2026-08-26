# Maintaining this theme

Notes for whoever edits this repo next. The README is for people installing the
theme; this is for people changing it.

Everything here was verified on Omarchy 4.0.0 / Hyprland 0.56 / Qt 6.11.

---

## How the pieces fit together

Three separate things ship in this repo, and they reach the user by three
different routes:

| What | Lives in | How it gets installed | When |
| --- | --- | --- | --- |
| Palette, wallpaper, previews | repo root, `backgrounds/` | `omarchy theme install`, then `omarchy-theme-set` | on theme switch |
| Screensaver **artwork** | `screensaver/` | copied along with the theme | on theme switch |
| Screensaver **renderer** | `screensaver-engine/` | `./screensaver-engine/install.sh`, by hand | once, opt-in |
| Typora stylesheet | `typora/` | `cp` by hand | once, opt-in |

The key mechanism: `omarchy-theme-set` does

```bash
cp -r "$USER_THEMES_PATH/$THEME_NAME/"* "$NEXT_THEME_PATH/"
```

— the *entire* theme directory, not a known list of files. That is why a theme
can carry arbitrary payload like `screensaver/`, and why `assets/` also ends up
in `~/.local/state/omarchy/current/theme/`. Harmless (the copy is reflinked on
btrfs, measured at ~21 ms), just be aware nothing is filtered.

The renderer decides whether to run by looking for the active theme's artwork:

```bash
THEME_ART="$HOME/.local/state/omarchy/current/theme/screensaver/title.txt"
```

Absent, it `exec`s Omarchy's stock screensaver. So the engine is installed
system-wide but only draws for themes that ship a `screensaver/` directory —
switching away needs no uninstall, and any other theme can opt in by adding one.

---

## Constraints that are not obvious

### 1. `colors.toml` comments must be on their own line

Omawrite parses `colors.toml` itself and **gives up on a trailing comment after
a value**:

```toml
accent = "#b26ac6"    # orchid    <- breaks Omawrite
```

It then falls back to a grey around `#51545C`, which is nearly invisible against
this background — every colour in the file is lost, not just the annotated one.
Full-line comments are fine, including the block at the top.

Inline comments are valid TOML and every other Omarchy consumer handles them.
None of the 22 stock themes use them, which is why the bug is unreported.

To confirm a suspicion: Omawrite repaints live on `omarchy theme set`, so open a
document, flip between this theme and a stock one, and sample the text pixels.

### 2. Anything Quickshell draws must be PNG or JPEG — never WebP

Qt on Arch has no WebP image plugin:

```bash
ls /usr/lib/qt6/plugins/imageformats/     # gif, ico, jpeg, pdf, svg -- no webp
```

`libwebp-utils` does not help; it ships `cwebp`/`dwebp` only, no Qt plugin. This
bites two files, because Quickshell renders both:

- `backgrounds/*` — the desktop background
- `preview.png` — the theme switcher tile, which `omarchy-theme-switcher`
  merely symlinks into its cache without converting

Both filename globs *accept* `.webp`, which makes it look supported. It is not:
the switcher tile renders blank. JPEG is safe and much smaller than PNG if size
matters (`preview.png` is 2.2 MB as PNG, ~620 KB at JPEG q92; the wallpaper
ships as JPEG q90 4:4:4 for the same reason — 1.0 MB vs 4.0 MB as PNG at
38.9 dB PSNR).

A `libqwebp.so` plugin *can* exist via the `qt6-imageformats` package — but on
Arch that package is optional and no Omarchy component requires it, so a theme
must not count on it. PNG or JPEG only for anything Quickshell draws.

`assets/` is free to be WebP — only GitHub renders those.

### 3. The PATH override must be last, and unconditional

Omarchy's screensaver chain is package-owned end to end: the shell's idle service
hardcodes `omarchy-launch-screensaver`, which resolves `omarchy-screensaver` off
`PATH`. There is no hook and no config knob, so shadowing that name is the only
override point. `install.sh` appends a block to `~/.config/hypr/hyprland.lua`,
bracketed by sentinel comments:

```lua
-- >>> omarchy-nujabes-screensaver
...
-- <<< omarchy-nujabes-screensaver
```

The sentinels are the *only* coupling between install and uninstall: the
uninstaller deletes whatever sits between them (plus the pre-sentinel legacy
form, for old installs), so the block's wording and code are free to change.
Re-running `install.sh` converges: an intact, last-in-file block is left
alone; an outdated, superseded, or mangled-but-recognizable block is replaced
with a fresh one at the end of the file; marker leftovers it cannot identify
abort before anything is written. The uninstaller is also copied into
`~/.local/share/omarchy-nujabes-screensaver/`, so removing the theme checkout
never strands the override.

Every config edit backs `hyprland.lua` up first (`hyprland.lua.bak.<epoch>`,
the five newest are kept) and restores the backup automatically if the edit
fails partway.

Two things about that block are load-bearing:

- It must be the **last** `hl.env("PATH", ...)` call in the file.
- It must run **unconditionally**. `/usr/share/omarchy/default/hypr/envs.lua`
  rebuilds `PATH` with `/usr/share/omarchy/bin` forced to the front on *every*
  parse. A `if not already present` guard looks correct and silently fails on the
  **second** `hyprctl reload`: the directory is already in the process environ,
  the guard skips, and Omarchy's line wins.

So verify after two or three reloads, never one:

```bash
for i in 1 2 3; do hyprctl reload; sleep 1; done
hyprctl dispatch 'hl.dsp.exec_cmd([[bash -c "command -v omarchy-screensaver > /tmp/x"]])'
cat /tmp/x    # must be the ~/.local/share/... path
```

### 4. Optional theme files

All 22 stock themes ship `preview.png`, `preview-unlock.png` and `unlock.png`;
community themes routinely ship none. Each degrades independently:

| Missing | Consequence |
| --- | --- |
| `preview.png` | switcher falls back to the first image in `backgrounds/` |
| `preview-unlock.png` | theme absent from `omarchy plymouth list` and the Plymouth switcher |
| `unlock.png` | no Plymouth boot theming at all (`omarchy plymouth set by theme`) |

This theme ships only `preview.png`. The other two were dropped deliberately —
Plymouth theming is opt-in and needs root, and the two images cost 4.7 MB.

Note `unlock.png` is the **Plymouth boot logo**, despite the name. It is not the
lock screen. `omarchy-plymouth-current` identifies the active boot theme by
`cmp -s` against each theme's copy, so it must stay byte-identical — another
reason not to re-encode it.

---

## Regenerating the artwork

The braille art is transcoded from the wallpaper's own title, so the lettering is
the real thing rather than a substitute font. Both commands below were re-run
against the committed files and reproduce them exactly.

```bash
W=backgrounds/1-nujabes.jpg          # 2560x1440; crops assume that size

magick "$W" -crop 880x118+1145+66  +repage /tmp/nuj.png
magick "$W" -crop 640x92+1255+196  +repage /tmp/kana.png

omarchy transcode ascii /tmp/nuj.png  screensaver/title.txt --width 110 --height 10 --invert --threshold 20
omarchy transcode ascii /tmp/kana.png screensaver/kana.txt  --width 96  --height 12 --invert --threshold 18
```

Then strip leading and trailing blank lines from each file — the transcoder pads
to the requested height.

`title.txt` needs one manual cleanup afterwards. The transcode picks up three
smoke specks around the `S`: two detached dots off its top-right corner and one
floating inside the lower bowl. Remove them:

```python
L = open("screensaver/title.txt", encoding="utf-8").read().split("\n")
for row, col in ((0, 101), (1, 101), (5, 98)):     # 0-indexed
    l = list(L[row]); l[col] = " "; L[row] = "".join(l).rstrip()
open("screensaver/title.txt", "w", encoding="utf-8").write("\n".join(L))
```

Things learned tuning this, if you change the crops:

- **Only the lettering transcodes well.** The face and the vinyl records turn to
  mush at any width that fits a terminal — they are too finely detailed for
  1-bit braille. Do not bother.
- **Braille (default), not `--mode block`.** Block mode is gapless but halves the
  vertical resolution; the letters come out chunky and the `A` deforms.
- **Threshold matters more than width.** The title needs ~20; the katakana has
  thinner, more distressed strokes and needs ~18 with a wider canvas.

### The small title

`screensaver/title-small.txt` is different: hand-drawn half-block art (2 lines,
33 columns), not a transcode. The renderer walks the title variants widest-first
and draws the first one that fits, so this is what "NUJABES" looks like on a
window under ~103 columns (a 1366px panel, or HiDPI at scale 2). Keep any
replacement inside plain block glyphs (`█ ▄ ▀`) — the smoke sweep colors every
non-space character.

### The palette image

`assets/palette.webp` is generated from `colors.toml` by `assets/make-palette.py`,
which is **git-ignored** — it is maintainer tooling, not something an installer
needs. That means a fresh clone cannot regenerate the image. If you have edited
the palette and no longer have the script, it renders grouped swatches
(base / text / accent / normal / bright) via SVG, rasterises with `rsvg-convert`
and encodes with `cwebp`. The same script also syncs the `--nj-*` palette block
in `typora/nujabes.css` from `colors.toml` — so on a clone without the script,
a palette edit means updating that CSS block by hand too.

### The screensaver recording

`assets/screensaver-sample.webp` is an **animated** WebP, not a video, and that is
deliberate. `raw.githubusercontent.com` serves every blob in the repo as
`application/octet-stream` under a `sandbox` CSP, so a committed `.mp4` can never
play in the README — it only downloads. Animated WebP renders inline from plain
`![]()`, and 720 wide at 12 fps reads fine for footage this slow.

Record at full resolution, then:

```bash
ffmpeg -i <recording>.mp4 -vf "fps=12,scale=720:-2" \
  -c:v libwebp_anim -q:v 80pl -compression_level 5 -loop 0 -an \
  assets/screensaver-sample.webp
```

Keep it under ~3 MB (the 38 s clip lands at ~2.5 MB); it autoplays for
everyone who opens the README.

---

## Tuning the screensaver

Constants at the top of `screensaver-engine/nujabes_screensaver.py`:

```python
FPS = 14.0                 # ~2-3% of one core at this rate (was ~11% before
                           # the table-driven pass; the terminal emulator
                           # pays its own share for the escape stream)
SMOKE_WAVELENGTH = 102.0   # columns per accent -> warm -> accent band
SMOKE_DRIFT = 0.50         # rad/s; full colour cycle = 2*pi/this, ~12.6 s
CLOUD_WIND = 0.42          # cloud drift; ~6 cells/s, ~35 s to cross a screen
CLOUD_ENTRAIN = 0.30       # how strongly the record's spin carries the cloud
```

`SMOKE_WAVELENGTH` is deliberately close to the title's width in columns, so one
full period spans it and both colours are on screen at once. Widen it and the
whole word sits in one tint.

Rendering decisions worth not re-litigating:

- **Vertical colour variation is kept near zero.** One braille cell is four dot
  rows but only *one* colour, so a real vertical gradient paints the letters in
  horizontal bands and the strokes read as sliced. The `y` term in `frame()`'s
  smoke field is 0.025 for that reason.
- **The record's grooves are not dithered.** Ordered dithering dissolves the
  concentric rings into noise; the ridge itself decides the dot. Subpixels are
  classified (label / rim / groove) once in `build_record`, so the per-frame
  cost per subpixel is table lookups and adds — no radius math, no trig.
- **Every rotating cue on the record is 1-fold** — one dominant sheen (the
  opposed one stays faint), a single-lobe eccentric groove warp, one soft
  lobe on the label. Symmetric cues (near-equal opposed sheens, a 3-lobed
  warp) make the pattern nearly repeat every half or third turn, and the spin
  visibly stalls or reverses as the eye latches onto the next arc — measured
  at 8 apparent reversals per 240 frames before the change, none after.
- **Colours come from lookup tables.** Text colors are a
  `[brightness][sweep position]` LUT with the easing curve baked in; the record
  reads a quantized vinyl ramp. The quantization steps (`SMOKE_UQ`, `SMOKE_KQ`,
  `VINYL_Q`) are finer than 8-bit truecolor survives, and the coarser color
  runs are what make `paint()`'s SGR dedup effective.
- **The cloud is particles in a potential-flow field, not a texture.** Each
  wisp is a few hundred braille dots advected by uniform wind plus the exact
  flow-past-a-cylinder solution at the record (with a tangential term for its
  spin), so it parts around the disc, rides the rotation near the rim, and
  rejoins downstream — no trig per particle, and the deformation is physics
  rather than animation. It draws only into empty cells, so it always sits
  behind the lettering and the record. A wisp is emitted at the record's rim
  (it starts half-hidden behind the disc and seeps out), wraps
  particle-by-particle at the screen edges instead of dying there, and lives
  `CLOUD_LAPS` screen-widths before fading so diffusion never smears it into
  uniform haze.
- **Focus loss arrives from Hyprland's event socket** (`.socket2.sock`), not
  polling; a 1 Hz `hyprctl` poll remains only as the fallback when the socket
  is unavailable, and that poll fails *toward exiting* after five consecutive
  errors — a screensaver that stops noticing focus loss is worse than one that
  leaves early.

Colours come from the active theme's `colors.toml` — the parse whitelist is
exactly the `FALLBACK` table in the renderer (`background`,
`bright_foreground`, `accent`, `orange`, `muted`, `selection`) — so the
renderer is theme-agnostic; only the artwork is Nujabes-specific. A theme that
defines colors but no `orange` gets one derived from its own
`accent`/`bright_foreground` rather than inheriting Nujabes amber.

---

## Testing

```bash
omarchy theme set nujabes                    # apply
omarchy launch screensaver force             # screensaver on demand
./screensaver-engine/uninstall.sh            # full round trip
./screensaver-engine/install.sh              # idempotent, safe to re-run
```

Traps that cost real time:

- **The renderer cannot be run bare.** `python3 nujabes_screensaver.py` in a
  working terminal exits after ~1 s: the focus watcher sees a focused window
  whose class is not `org.omarchy.screensaver` and correctly calls that focus
  lost. Tune through `omarchy launch screensaver force` instead — it launches
  the dedicated window with the right class.
- **`pkill -f 'org.omarchy.screensaver'` matches your own shell.** The pattern
  appears in your command line, so `pkill -f` kills the shell running it and
  `omarchy-launch-screensaver`'s own `pgrep -f` guard sees a false positive and
  exits early. Use a bracket: `pkill -f '[o]rg.omarchy.screensaver'`, and keep
  the literal string out of test command lines.
- **Typora's process is `Typora`, capital T.** `pkill -x typora` matches nothing,
  and re-launching just reuses the running instance — so CSS edits appear not to
  load. Typora only reads themes at startup.
- **Window geometry goes stale.** Hyprland retiles when other windows come and
  go, so a screenshot crop from cached `at`/`size` silently captures the wrong
  region. Re-read `hyprctl clients -j` on every capture, and select by `.class`,
  not `.title` — a terminal's title often contains the filename you are matching.

### Before committing screenshots

Screenshots of a working desktop leak usernames, paths and IPs. Sweep with OCR
rather than by eye:

```bash
magick assets/setup.webp -colorspace gray -resize 150% /tmp/o.png
tesseract /tmp/o.png - --psm 6 | grep -inE "yourname|/home|192\.168"
```

`btop` is the usual offender: its `User:` column repeats the username on every
row, and the net box shows the LAN address. Also check image metadata — GIMP
writes `Software`/XMP tags on export by default:

```bash
magick identify -verbose <file> | grep -iE "exif|xmp|software|artist|creator"
```

---

## Release checklist

1. `python3 assets/make-palette.py` if `colors.toml` changed — this renders
   `assets/palette.webp` *and* syncs the `--nj-*` block in
   `typora/nujabes.css`, which nothing else checks.
2. Re-run the transcodes if the wallpaper changed, plus the `S` cleanup.
3. OCR sweep any new screenshot; check metadata.
4. Verify every README/NOTICE link resolves.
5. Full dry run into a clean state:
   ```bash
   ./screensaver-engine/uninstall.sh
   rm -rf ~/.config/omarchy/themes/nujabes
   omarchy theme install <repo-url-or-local-path>
   cd ~/.config/omarchy/themes/nujabes && ./screensaver-engine/install.sh
   omarchy launch screensaver force
   ```
   Then switch to a stock theme and confirm the screensaver falls back to `ttfx`
   rather than erroring.
6. Update `NOTICE.md` if files were added or removed — it enumerates which paths
   are and are not covered by the MIT licence.

---

## Listing on omarchy.org

Getting the theme onto <https://omarchy.org/themes/> is a pull request against
`omacom-io/omarchy-site`. An automated review (omarchybot) checks the *theme
repository*, not the diff, against the rules Omarchy enforces in code — all
verified against `stage_installed_theme` in `bin/omarchy-theme-set` and the
bot's comments on merged PRs:

1. **No `*.lua` anywhere** and no `alacritty.toml` / `foot.ini` /
   `ghostty.conf` / `kitty.conf` / `vscode.json`. Staging drops them from a
   repo-installed theme (each can name a program to run) and regenerates them
   from `colors.toml` — they work locally, then silently do nothing for
   installers. Symlinks are dropped too.
2. **`preview.png` present**, or the switcher shows a bare wallpaper.
3. **Backgrounds cheap to clone**: hard flag over 4 MB *or* over
   0.5 bytes/pixel per image; soft flags over 2.5 MB or wider than 3840px.
   (This is why the wallpaper is a 1.0 MB JPEG.)
4. **Shipped scripts get an informational flag** ("worth a look, not holding
   anything up") — `screensaver-engine/` will draw one; the README's framing
   of it as opt-in and separate from the theme is the answer.
5. **The PR itself**: add `assets/themes/nujabes.webp` (grid tile, ~1200x675,
   made from `preview.png`) and a `<figure class="themes__theme">` block in
   `themes/index.html`, in alphabetical position, matching the neighbors'
   markup exactly. One theme name and one asset filename per theme — the bot
   flags collisions across open PRs.
