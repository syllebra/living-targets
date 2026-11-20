# LIVING TARGETS

Animated CSS targets for projection systems — 40 "living" shapes, high contrast
on pure black, with a full arcade sound kit. No build step, no dependencies:
plain HTML, CSS and JavaScript.

**▶ Live demo: https://syllebra.github.io/living-targets/**

| page | what it is |
|---|---|
| [`index.html`](index.html) | the wall — 40 targets in a grid, entrance + idle + hit |
| [`solo.html`](solo.html) | one target, fullscreen black: drag, free x/y resize, colour, sound |
| [`warp.html`](warp.html) | deformation playground — corner pin + free-form |
| [`solofull.html`](solofull.html) | **the solo page as ONE offline file** (CSS + JS + audio inlined) |
| [`audio/index.html`](audio/index.html) | auditioner for the 55-sound kit (A/B dry-wet) |

---

## Each target has three animations

1. **Entrance** — 15 variants (`pop`, `unfold`, `drop`, `spin`, `iris`, `bloom`,
   `glitch`, `shutter`, `tilt`, `rise`, `split`, `stamp`, `warp`, `flicker`,
   `slide`), staggered so neighbours never arrive the same way.
2. **Idle** — a permanent loop: breathing, heartbeat, radar sweep, flicker,
   jelly, twinkle, shiver…
3. **Hit** — a unique reaction on click (punch, squash, shake, spin, zap,
   glitch, invert, drop, blow, tremble, pop) plus shockwave, sparks and flash.

## Shapes

Circle, round box, square, rectangle, pill, tall box, hexagon, diamond,
octagon, star, triangle — drawn with `border-radius` / `clip-path`, so effects
clip perfectly inside the shape.

---

## solo.html — projection mode

- **Fullscreen black**, one target, drag to place it.
- **Independent x/y size**: `width px` / `height px` fields, edge handles
  (`n/s/e/w` act on one axis only, so you get a true elongated rectangle),
  wheel = uniform, `shift`+wheel = width, `alt`+wheel = height.
- **Corner pin** (`C`): four yellow corners warp the target projectively —
  keystone, shear, rotation. A real homography applied as `matrix3d`, so it
  stays vector: no raster, animations keep their quality.
- **Colour**: 14 named swatches + continuous hue wheel.
- **Sound**: all 55 sounds, per-target picking.
- **Projection keys**: `H` hide the UI, `F` fullscreen, `Space` hit,
  `R` replay entrance, `M` mute, `C` corner pin.
- Everything persists in `localStorage`, so a projector setup survives a reload.
- Opening/closing the panel **never moves the target**: its screen position is
  anchored and only corrected if the new layout would clip it.

## Mobile

Both `solo.html` and `warp.html` detect mobile automatically and live
(`matchMedia(max-width:760px, pointer:coarse)`, re-evaluated on rotation).
The panel becomes a bottom sheet and the work area shrinks to the strip above
it, so **you can drag the target while the controls stay open**. Tap targets are
enlarged (44 px restore button, 30 px corner handles).
Add `?mobile=1` to any URL to force that layout from a desktop.

---

## The sound kit

55 one-shots, generated from scratch in Python (no samples, no audio library):
additive oscillators, FM, filtered noise, bitcrush, then a hand-written
**Schroeder/Freeverb** stereo reverb (8 damped combs + 4 allpasses, right
channel offset by 23 samples → decorrelated tail).

- 16 clicks · 6 UI · 19 rewards · 5 impacts · 4 zaps · 5 ambiences
- 48 kHz, peak ≤ −1 dBFS, loudness-matched (8.5 dB spread across the whole kit,
  4.5 dB within the rewards) so any sound can be swapped without re-mixing
- WAV (lossless) + MP3. This repo ships MP3 to stay light; the generator
  produces both.

Regenerate:
```bash
python3 tools/gen-arcade-clicks.py audio      # writes WAV + MP3 + sprite + manifest
```

---

## Offline single file

`solofull.html` contains **everything**: stylesheet, engine and all 55 sounds as
base64. No network request at all — it works from `file://`, a USB stick, or a
projector laptop with no wifi.

```bash
python3 tools/build-solofull.py                # ~1.2 MB at 96 kbps
python3 tools/build-solofull.py --kbps 64      # ~0.9 MB
python3 tools/build-solofull.py --kbps 128     # ~1.5 MB
```

Verified with `fetch` and `XMLHttpRequest` disabled: 55/55 sounds decoded,
**zero network attempts**.

---

## Run locally

The pages need to be served over HTTP (audio loading is blocked on `file://` —
that is exactly what `solofull.html` solves):

```bash
python3 -m http.server 8123
# → http://localhost:8123/index.html
```

## Add a target

1. One line in `TARGETS` in `fx.js`:
   `[name, shape, colour, idleAnim, hitAnim, fxClass]`
2. A `.fx-<name>` rule plus its keyframes in `fx.css`.
3. A sound for that effect in `SFX_FOR_FX` (`fx.js`).

Both pages pick it up automatically, including the picker in `solo.html`.

## Architecture

```
fx.css          shapes, colours, fx layers, entrance/idle/hit keyframes
fx.js           FXKIT: TARGETS, create(), hit(), enter(), enterAll(), SFX
index.html      the wall (builds a grid of create())
solo.html       one target, drag / resize / colour / sound / corner pin
warp.html       corner pin + free-form (feDisplacementMap) experiments
audio/          the 55 sounds (MP3 here) + auditioner + manifest
tools/          sound generator and single-file builder
```

`.stage` = `halo + button.t + shock + spark container`. The button carries
`--idle` and `--hit`; the colour comes from `--c`. Entrance states are
`.anim-ready` (JS is alive) → `.is-in` (animating) → `.in-done` (releases
`clip-path`, so the iris/shutter variants cannot crop later effects).

## Licence

MIT — see [LICENSE](LICENSE).
