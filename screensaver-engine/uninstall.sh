#!/bin/bash
# SPDX-License-Identifier: MIT
# Removes the screensaver engine and the Hyprland PATH block that reaches it.
# The theme itself is untouched -- remove that with `omarchy theme remove`.

set -euo pipefail

DEST="$HOME/.local/share/omarchy-nujabes-screensaver"
HYPR="$HOME/.config/hypr/hyprland.lua"
MARKER="omarchy-nujabes-screensaver"

info() { printf '\033[32m==>\033[0m %s\n' "$1"; }

if [[ -d $DEST ]]; then
  rm -rf "$DEST"
  info "Removed $DEST"
fi

if [[ -f $HYPR ]] && grep -q "$MARKER" "$HYPR"; then
  backup="$HYPR.bak.$(date +%s)"
  cp "$HYPR" "$backup"
  info "Backed up hyprland.lua to $(basename "$backup")"
  # Drop from the block's leading comment through its hl.env("PATH", ...) line.
  python3 - "$HYPR" <<'PY'
import re
import sys

path = sys.argv[1]
text = open(path).read()
block = re.compile(
    r"\n*-- Screensaver override\..*?\nhl\.env\(\"PATH\", table\.concat\(kept, \":\"\)\)\n",
    re.S,
)
new, n = block.subn("\n", text)
if not n:
    sys.exit("could not find the block; remove it by hand")
open(path, "w").write(new)
PY
  info "Removed the PATH block from hyprland.lua"
fi

command -v hyprctl >/dev/null && hyprctl reload >/dev/null 2>&1 || true
info "Done -- the stock ttfx screensaver is back."
