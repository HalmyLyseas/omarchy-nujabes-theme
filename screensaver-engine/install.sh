#!/bin/bash
# SPDX-License-Identifier: MIT
# Installs the screensaver engine this theme's artwork is drawn by.
#
# The theme itself needs none of this -- colors, background and lock screen all
# work on a plain `omarchy theme install`. Run this only if you also want the
# animated screensaver.
#
# Safe to re-run: an intact install is left alone, and an outdated or broken
# PATH block is replaced with a fresh one.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.local/share/omarchy-nujabes-screensaver"
HYPR="$HOME/.config/hypr/hyprland.lua"
MARKER="omarchy-nujabes-screensaver"

info() { printf '\033[32m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[33m==>\033[0m %s\n' "$1" >&2; }

# One backup lands per config edit and nothing else ever removes them; keep
# the five newest. Only exact .bak.<epoch> names are ours to delete.
prune_backups() {
  local f n=0
  while IFS= read -r f; do
    [[ $f =~ \.bak\.[0-9]+$ ]] || continue
    ((++n <= 5)) || rm -f -- "$f"
  done < <(ls -t -- "$HYPR".bak.* 2>/dev/null)
}

# Every check runs before anything is written, so a refused install leaves no
# half-installed files behind.
command -v python3 >/dev/null || {
  warn "python3 not found -- the renderer needs it."
  exit 1
}
if [[ ! -f $HYPR ]]; then
  warn "No $HYPR -- this installer only knows Omarchy's Lua config."
  warn "Append the BLOCK string from $0 to your Hyprland config by hand;"
  warn "nothing has been installed."
  exit 1
fi

info "Installing the renderer into $DEST"
mkdir -p "$DEST/bin"
install -m 755 "$HERE/nujabes_screensaver.py" "$DEST/nujabes_screensaver.py"
install -m 755 "$HERE/bin/omarchy-screensaver" "$DEST/bin/omarchy-screensaver"
# The uninstaller ships with the install itself, so removing the theme
# checkout (`omarchy theme remove`) cannot strand the PATH override with no
# removal path left on disk.
install -m 755 "$HERE/uninstall.sh" "$DEST/uninstall.sh"

# Omarchy's idle service hardcodes `omarchy-launch-screensaver`, which resolves
# `omarchy-screensaver` off PATH. Shadowing that name is the only override
# point in a chain that is otherwise package-owned.
#
# The python below converges hyprland.lua on exactly one canonical block,
# sitting last so no later hl.env("PATH", ...) call can undo it: an intact
# install is left untouched, an older or superseded block is replaced, and
# marker leftovers it cannot safely identify abort before anything is written.
backup="$HYPR.bak.$(date +%s)"
cp "$HYPR" "$backup"
trap 'cp "$backup" "$HYPR"; warn "hyprland.lua was not changed (restored from backup)"' ERR
result="$(python3 - "$HYPR" <<'PY'
import re
import sys

path = sys.argv[1]
MARKER = "omarchy-nujabes-screensaver"
BEGIN = "-- >>> " + MARKER
END = "-- <<< " + MARKER

# uninstall.sh removes whatever sits between the sentinels, so the wording in
# between is free to change without the two scripts drifting apart.
BLOCK = BEGIN + """
-- Nujabes screensaver override. Omarchy's idle service runs
-- `omarchy-launch-screensaver`, which resolves `omarchy-screensaver` off PATH
-- -- the only override point in a chain that is otherwise package-owned.
-- Putting this directory ahead of /usr/share/omarchy/bin swaps in the custom
-- renderer; run ~/.local/share/omarchy-nujabes-screensaver/uninstall.sh (or
-- delete this block, sentinels included) to go back to the stock screensaver.
--
-- It must stay the LAST hl.env("PATH", ...) call and must run unconditionally:
-- default/hypr/envs.lua rebuilds PATH with /usr/share/omarchy/bin forced to the
-- front on every parse, so a `if not already present` guard silently loses the
-- name back to the stock screensaver on the second `hyprctl reload`.
local nujabes_screensaver = os.getenv("HOME") .. "/.local/share/omarchy-nujabes-screensaver/bin"
local kept = {}
for entry in (os.getenv("PATH") or "/usr/local/bin:/usr/bin"):gmatch("[^:]+") do
  if entry ~= nujabes_screensaver then table.insert(kept, entry) end
end
table.insert(kept, 1, nujabes_screensaver)
hl.env("PATH", table.concat(kept, ":"))
""" + END + "\n"

text = open(path).read()

# Intact canonical block, no stray copies, and nothing after it re-touching
# PATH: there is nothing to do.
if BLOCK in text and text.count(MARKER) == BLOCK.count(MARKER):
    tail = text.split(BLOCK, 1)[1]
    if 'hl.env("PATH"' not in tail:
        print("unchanged")
        raise SystemExit

# Strip every form this installer has ever written. Sentinel blocks go first:
# both forms end in the same hl.env line, so the legacy pattern (installs made
# before the sentinels existed) must only see what is left.
sentinel = re.compile(
    r"\n*" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n", re.S
)
legacy = re.compile(
    r"\n*-- Screensaver override\..*?"
    r"\nhl\.env\(\"PATH\", table\.concat\(kept, \":\"\)\)\n",
    re.S,
)
text, n_sent = sentinel.subn("\n", text)
text, n_legacy = legacy.subn("\n", text)
if MARKER in text:
    sys.exit(
        "hyprland.lua mentions %s outside any block this installer knows;"
        " clean that up by hand first (nothing was changed)" % MARKER
    )

if not text.endswith("\n"):
    text += "\n"
open(path, "w").write(text + "\n" + BLOCK)
print("refreshed" if n_sent or n_legacy else "installed")
PY
)"
trap - ERR

case "$result" in
  unchanged)
    rm -f "$backup"
    info "Hyprland already puts the override on PATH; leaving it alone."
    ;;
  installed)
    info "Appended the PATH block to hyprland.lua (backup: $(basename "$backup"))"
    ;;
  refreshed)
    info "Replaced the existing PATH block in hyprland.lua (backup: $(basename "$backup"))"
    ;;
esac
prune_backups

if command -v hyprctl >/dev/null && hyprctl version >/dev/null 2>&1; then
  hyprctl reload >/dev/null 2>&1 || true
  info "Reloaded Hyprland"
fi

cat <<EOF

Done. The renderer runs only for themes that ship a screensaver/ directory --
every other theme falls through to Omarchy's stock ttfx screensaver, so there is
nothing to undo when you switch away.

  Try it now:   omarchy launch screensaver force
  Uninstall:    $DEST/uninstall.sh

EOF
