Screensaver artwork for this theme.

Omarchy's `omarchy-theme-set` copies the whole theme directory into
`~/.local/state/omarchy/current/theme/`, so anything here lands beside
colors.toml when the theme is applied. The custom screensaver renderer in
`~/.local/share/omarchy-nujabes-screensaver/` looks for
`current/theme/screensaver/title.txt` and runs only when it is present --
any other theme falls through to Omarchy's stock ttfx screensaver.

  title.txt    braille art, drawn centred at the top
  kana.txt     braille art, drawn under the title
  tagline.txt  one line of plain text, drawn at the bottom (optional)

Colours are not stored here; they come from this theme's colors.toml
(`accent` and `orange` for the text, plus `muted`, `selection` and
`bright_foreground` for the record).
