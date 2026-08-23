# Assets

The mark is the **Torii Bracket**, chosen from six
directions. A shrine gate whose uprights read as a square bracket — the shape of
a citation marker, `[E1]`, which is what this system puts under every sentence
it writes.

| File | Use |
|---|---|
| `logo.svg` | the mark, on a light ground |
| `logo-dark.svg` | the mark, on a dark ground |
| `logo-mark.svg` | single colour, inherits `currentColor` |
| `favicon.svg` | 16px variant — tie-beam dropped, strokes thickened |
| `lockup.svg` / `lockup-dark.svg` | mark plus wordmark — **decks and slides only, see below** |

**Vermilion is `#BE3A28`** on light and `#E2543F` on dark. It is the pigment
torii gates are actually painted (朱色) and it is Arasaka red — one hex for both
halves of the name.

**Clear space:** never less than the distance between the two uprights.
**Below 20px, use `favicon.svg`.** The tie-beam closes the counters at small
sizes and the mark turns to mud — that is why the small variant drops it rather
than scaling the same drawing down.

## The lock-up carries live text, so it is not for the web

`lockup.svg` sets the word *mikoshi* as real `<text>` in IBM Plex Mono. An SVG
shown as an image **cannot load a webfont** — it can only use faces already
installed on the reader's machine — so on GitHub, and on most machines, that
wordmark silently falls back to whatever monospace is lying around. A logo that
renders differently per viewer is not a logo.

**So the README uses the mark alone above a real Markdown heading**, which is
robust everywhere and stays selectable text. Use the lock-up where you control
the fonts: decks, slides, a PDF.

**To make the lock-up safe for the web**, its text has to be converted to
outlines. That needs a font tool and has not been done; until it is, treat the
lock-up as a working file rather than a shipped asset.
