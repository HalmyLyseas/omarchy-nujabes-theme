---
title: Nujabes for Typora
author: a sample document
---

# Nujabes for Typora

A sample document, useful for seeing every part of the theme at once — open it
in Typora with **Nujabes** selected under *Themes*.

> Your beat still laments the world.

Body text mixes **bold**, *italic*, ***both at once***, `inline code`,
~~strikethrough~~ and a [link to omarchy.org](https://omarchy.org).
Footnotes[^1] hang off the bottom of the page.

## Headings

### Third level

#### Fourth level

##### Fifth level

###### Sixth level, the quietest of them

## Lists

- Fire arc — yellow into orange into red
- Violet arc — indigo into orchid into magenta
  - Nested one level
    - And another, for depth
- Near-black underneath it all

1. First the record settles
2. Then the sheen sweeps across
3. Then the dust drifts up

- [x] Palette derived from the wallpaper
- [x] Screensaver artwork traced from the title
- [ ] Track down the original artist

## Code

Inline snippets like `SMOKE_DRIFT = 0.50` sit in amber. Fenced blocks carry the
full highlighting:

```python
def smoke_color(self, x, y, t, weight):
    """Purple/gold drifting sideways across the lettering."""
    u = (
        0.5
        + 0.38 * math.sin(x * SMOKE_K - t * SMOKE_DRIFT + y * 0.025)
        + 0.12 * math.sin(x * 0.011 + t * 0.30)
    )
    v = 2.0 * u - 1.0
    v = math.copysign(abs(v) ** 0.62, v)          # hold each colour longer
    breath = 0.88 + 0.12 * math.sin(t * 0.34 + x * 0.006)
    return dim(ramp(self.smoke, 0.5 + 0.5 * v), weight * breath)
```

```bash
# Install the theme, then the optional screensaver
omarchy theme install https://github.com/HalmyLyseas/omarchy-nujabes-theme.git
./screensaver-engine/install.sh
```

```css
:root {
  --nj-accent: #b26ac6;   /* orchid, the violet smoke */
  --nj-orange: #e88b2c;   /* the amber strands */
}
```

## Tables

| Role | Hex | Where it comes from |
| --- | --- | --- |
| `accent` | `#b26ac6` | orchid, the violet smoke |
| `orange` | `#e88b2c` | the amber strands |
| `foreground` | `#e3dcda` | bone |
| `green` | `#9aa06b` | olive foliage, left panel |
| `background` | `#0d0a11` | near-black behind everything |

## Rules and quotes

Quotes nest, and dim as they go:

> Hues 75–240 are entirely absent from the artwork.
>
> > So `cyan` is a muted steel-teal, chosen to stay legible next to `blue`
> > without inventing a colour the wallpaper never uses.

---

That horizontal rule, and this closing paragraph, are the last of it.

[^1]: Which are styled too, in case you use them.
