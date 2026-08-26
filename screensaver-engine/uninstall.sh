#!/bin/bash
# SPDX-License-Identifier: MIT
# Removes the screensaver engine and the Hyprland PATH block that reaches it.
# The theme itself is untouched -- remove that with `omarchy theme remove`.

set -euo pipefail

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

# hyprland.lua is edited first and the payload deleted last, so a failure at
# any point leaves PATH pointing at a directory that still exists.
removed_block=no
if [[ -f $HYPR ]] && grep -q "$MARKER" "$HYPR"; then
  command -v python3 >/dev/null || {
    warn "python3 not found -- it is needed to edit hyprland.lua; nothing was removed."
    exit 1
  }
  backup="$HYPR.bak.$(date +%s)"
  cp "$HYPR" "$backup"
  trap 'cp "$backup" "$HYPR"; warn "hyprland.lua was not changed (restored from backup)"' ERR
  python3 - "$HYPR" <<'PY'
import re
import sys

path = sys.argv[1]
MARKER = "omarchy-nujabes-screensaver"
# Sentinel blocks go first: both forms end in the same hl.env line, so the
# legacy pattern (installs made before the sentinels existed) must only see
# what is left.
sentinel = re.compile(
    r"\n*" + re.escape("-- >>> " + MARKER) + r".*?"
    + re.escape("-- <<< " + MARKER) + r"\n",
    re.S,
)
legacy = re.compile(
    r"\n*-- Screensaver override\..*?"
    r"\nhl\.env\(\"PATH\", table\.concat\(kept, \":\"\)\)\n",
    re.S,
)
text = open(path).read()
text, n_sent = sentinel.subn("\n", text)
text, n_legacy = legacy.subn("\n", text)
if MARKER in text:
    sys.exit(
        "hyprland.lua mentions %s outside any block this script knows;"
        " remove it by hand (nothing was changed)" % MARKER
    )
if not (n_sent or n_legacy):
    sys.exit("could not find the PATH block; remove it by hand (nothing was changed)")
open(path, "w").write(text)
PY
  trap - ERR
  info "Removed the PATH block from hyprland.lua (backup: $(basename "$backup"))"
  removed_block=yes
  prune_backups
fi

if [[ -d $DEST ]]; then
  rm -rf "$DEST"
  info "Removed $DEST"
fi

command -v hyprctl >/dev/null && hyprctl reload >/dev/null 2>&1 || true

if [[ $removed_block == yes ]]; then
  info "Done -- the stock ttfx screensaver is back."
else
  info "Done. No PATH block was found in hyprland.lua; if you put it in a"
  info "different config file by hand, remove it from there yourself."
fi
