#!/bin/bash
# SPDX-License-Identifier: MIT
# Installs the screensaver engine this theme's artwork is drawn by.
#
# The theme itself needs none of this -- colors, background and lock screen all
# work on a plain `omarchy theme install`. Run this only if you also want the
# animated screensaver.
#
# Safe to re-run: every step checks before it writes.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.local/share/omarchy-nujabes-screensaver"
HYPR="$HOME/.config/hypr/hyprland.lua"
MARKER="omarchy-nujabes-screensaver"

info() { printf '\033[32m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[33m==>\033[0m %s\n' "$1" >&2; }

command -v python3 >/dev/null || {
  warn "python3 not found -- the renderer needs it."
  exit 1
}

info "Installing the renderer into $DEST"
mkdir -p "$DEST/bin"
install -m 755 "$HERE/nujabes_screensaver.py" "$DEST/nujabes_screensaver.py"
install -m 755 "$HERE/bin/omarchy-screensaver" "$DEST/bin/omarchy-screensaver"

# Omarchy's idle service hardcodes `omarchy-launch-screensaver`, which resolves
# `omarchy-screensaver` off PATH. Shadowing that name is the only override
# point in a chain that is otherwise package-owned.
if [[ ! -f $HYPR ]]; then
  warn "No $HYPR -- add the PATH block from the README by hand."
  exit 1
fi

if grep -q "$MARKER" "$HYPR"; then
  info "Hyprland already puts the override on PATH; leaving it alone."
else
  backup="$HYPR.bak.$(date +%s)"
  cp "$HYPR" "$backup"
  info "Backed up hyprland.lua to $(basename "$backup")"
  cat >>"$HYPR" <<'LUA'

-- Screensaver override. Omarchy's idle service runs `omarchy-launch-screensaver`,
-- which resolves `omarchy-screensaver` off PATH -- the only override point in a
-- chain that is otherwise package-owned. Putting this directory ahead of
-- /usr/share/omarchy/bin swaps in the custom renderer; delete this block to go
-- back to the stock ttfx screensaver.
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
LUA
  info "Appended the PATH block to hyprland.lua"
fi

if command -v hyprctl >/dev/null && hyprctl version >/dev/null 2>&1; then
  hyprctl reload >/dev/null 2>&1 || true
  info "Reloaded Hyprland"
fi

cat <<EOF

Done. The renderer runs only for themes that ship a screensaver/ directory --
every other theme falls through to Omarchy's stock ttfx screensaver, so there is
nothing to undo when you switch away.

  Try it now:   omarchy launch screensaver force
  Uninstall:    $HERE/uninstall.sh

EOF
