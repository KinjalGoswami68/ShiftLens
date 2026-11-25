# ShiftLens design system v2: liquid chrome

Approved 2026-11-11; replaces the v1 flat dark system (in git history). One system for the
landing page (`/`) and the app (`/app/`). Live preview: `web/design-preview.html`.
Implementation: tokens and components in `web/src/styles.css`, theme controls in
`web/src/theme.ts`, landing-only pieces in `web/src/landing/`.

Source: the ui-ux-pro-max skill had no matching style ("Liquid Glass" is glassmorphism,
"Y2K" is neon pink), so the visual system is designed to the brief. From the skill:
Space Grotesk as the display face (its "Tech Startup" pairing), and the magnetic-button preset
with its limit of 1-2 magnetic elements per screen.

## Landing page v3 (2026-11-24): editorial + the inspector

Supersedes the landing-page parts below (cards, stat tiles, pill, sequence, theme controls on
the landing page). The app keeps v2 unchanged. Built in `web/src/landing/`.

- Dark only (near-black `#07080A` and silver); green only as the tiny live dot, red only for
  incidents / the new version's answers. 12-column grid, left-aligned, thin 1 px rules, corner
  ticks, numbered mono labels, radius 0-4 px. No cards.
- Type: Space Grotesk (wordmark, tagline), Instrument Serif 400 + italic (scene sentences, section
  heads, numbers; SIL OFL, self-hosted), JetBrains Mono (labels, ids, readouts), IBM Plex Sans (body).
- Hero: the wordmark fills the width (`100cqw / 4.06`; phones stacked "Shift / Lens",
  `100cqw / 2.08`), chrome + compositor-only sweep. Behind/through it the inspector
  (`inspector.ts`, 2D canvas back + front): answers drift away, a lens marks them red, label
  `<version> · <n> answers`. Readouts from one dataset (`data.ts`): live demo inside 09:00-21:00
  IST, else the recorded incident ("recorded 12 nov").
- Scenes (`scenes.ts`, CSS sticky + one passive scroll listener, no GSAP): 1 normal cluster, 2 the real trace
  (red from the version marker; threshold above every point because none crossed), 3 one red
  group under the lens, 4 the note word by word with its cited ids.
- Honesty rules: red = on the new version (per answer, not by position); never shown crossing the
  threshold unless an answer did; dates only, never clock times.

### Smoothness rules (measured 2026-11-24, `measure` harness: rAF + long-animation-frame per section)

| Rule | Why (measured) |
|---|---|
| No root CSS variable per pointer move on the landing (`trackPointer` not used) | restyled the whole page and repainted the 300 px wordmark on every mouse move |
| Reveals are opacity on spans built once; only the visible scene's words keep `will-change: opacity` (note words none) | promoting/demoting a word per transition re-rasterised the 100 px sentence: GPU raster dominated scroll frames (scenes 32 -> 59 fps on laptop); a layer for every word (~130) made each frame's commit expensive on phones |
| One passive scroll listener, one update per frame, `scrollY` read first, all offsets cached (re-measured on real resizes only) | ScrollTrigger's scroll reads after our class changes forced a style recalculation every frame (~500 ms per pass on the 4x phone); GSAP is no longer used (-45 KB) |
| No animated `filter: blur`; product screens use transform + opacity only | blur re-applied every scroll frame |
| No full-screen `mix-blend-mode` layers (grain removed on landing, cursor not blended) | full-screen blend over canvases that change every frame |
| Stage: pixel ratio <= 1.5 (phones 1.25), 480 points (phones 150), phones 30 fps, one loop, stops when hidden / off-screen / idle 30 s | fill rate and CPU |
| Light mode: if > 20 % of 90 frames are slow, the stage halves its points, goes to DPR 1 and 30 fps, and the ripple and sweeps stop (`html.lite`) | safety net for slow devices |
| Stage created at load, before the first paint | deferring it put the first GPU canvas setup into Lighthouse TBT (68 -> 829 ms) |
| Ripple background: laptops only | ~1 fps on laptop; on phones a touch handler + WebGL redraw per scroll frame while the points already move |

## Identity

brand word); everything you read sits on quiet graphite. Status colours keep their meaning
(green healthy / ready, amber provisional / SEV3, red incidents only) and are the only
saturated colours, plus one icy cyan accent for focus and links.

No glassmorphism, no blur, no gradient blobs, no glitter. Grain is a fixed 160 px SVG noise
at 5-9 % opacity.

## Theme: two modes x intensity

| | dark (default) | light |
|---|---|---|
| background, intensity 0 -> 100 | `#050506` -> `#1C1D21` | `#D8D9D7` -> `#F7F7F5` |
| surface | `#0E0F12` -> `#26272C` | `#E5E6E4` -> `#FFFFFF` |
| text / muted / subtle | `#F2F3F5` / `#A9ADB5` / `#8E929B` | `#111214` / `#43464D` / `#575B63` |
| accent (focus, links) | `#86DBFF` | `#075A7F` |
| ok / warn / bad | `#3DDC84` / `#F5B544` / `#FF6B6B` | `#0D6332` / `#7D5200` / `#B4232B` |

Intensity (`--i`, 0-1, default 0.5) mixes background and surface between the two ends
(`color-mix` in oklab), and scales reflections, grain and the liquid background. Contrast
was computed at intensity 0, 0.5 and 1 on both background and surface: every text colour is
at least 4.5:1 everywhere (worst: dark subtle 4.78, light ok 5.21). The chrome headline in
light mode uses a graphite chrome whose lightest stop `#6F747C` is at least 3.32:1 (large
darkest stop `#B9BEC5` gives 10.47:1 with `#0B0C0E`.

Controls: a round light/dark toggle, and the intensity fader (a vertical hardware slider
docked at the right edge on desktop, a short horizontal one in the header on mobile).
Keyboard: arrows / Page Up / Down. Both are stored in `localStorage`
(`shiftlens.theme`, `shiftlens.intensity`) and applied before first paint, so no flash.

## Type

| Role | Font | |
|---|---|---|
| brand word, headings, buttons, stat values | Space Grotesk 500-700 | brand: 46-184 px, -0.055em |
| body, tables, notes | IBM Plex Sans 400-600 | kept: long text stays readable |
| ids, numbers, ASCII, labels, terminal | JetBrains Mono 400-500 | |

All three are free (SIL OFL) and self-hosted as latin woff2 in the real sites.

## Motion and interaction (what each effect is for)

| Effect | Where | Rule |
|---|---|---|
| Liquid chrome background (WebGL, one fragment shader, fixed behind the whole page) | landing page | starts on the first mouse movement, touch or scroll (never at load); ripples follow the pointer and touches, scrolling stirs and drifts the field; keeps flowing 4 s after the last input, then stops; stops while the tab is hidden. Desktop 0.4x resolution; phones DPR capped at 1, 0.32x, at most 30 fps. Output clamped (dark <= 0.155, light >= 0.83) so text on the bare background stays AA. Never on reduced motion or software WebGL (SwiftShader, llvmpipe): static gradient instead. |
| Edge light on panels | stat tiles, feature cards (hover) | border-only highlight under the pointer |
| Magnetic pull + springy press | the one primary CTA per screen | skill preset: at most 1-2 per screen |
| Click pulse | buttons | 0.6 s ring in the accent colour |
| ASCII cursor (`+`, becomes a ring with `[open]` / `[press]` / `[drag]` / `[zoom]`) | landing page only, fine pointers only (the app keeps the normal cursor) | native cursor kept for text fields |
| ASCII decode of the brand word | landing hero, once, mouse/trackpad only | 0.7 s in plain colour, chrome applied at the end; the real text is in the page from the start, so it is not a preloader blocking content |
| Chrome brand word (gradient clipped to text) | landing hero, screens >= 900 px | below 900 px solid bright silver with a darker lower half (a static clipped copy: a chrome horizon); in software rendering the clipped gradient cost ~1 s of blocking time on Lighthouse mobile |
| Shine sweep (compositor-only: a window and the text copy inside it move in opposite directions, transform only) | landing: brand word, logo mark, primary button; 7 s cycle, staggered | paused while the hero is off screen; hidden under reduced motion. Measured 2026-11-22: 0 paints / 0 layouts in 4 s of sweeping |
| Tap pulse (press + fading ring, `scale`/`transform`/`opacity`) | landing buttons and cards, touch only | |
| Scroll reveals (rise + fade, staggered 70 ms) | landing sections; app: first load of a screen only | once per element |
| Pinned scroll sequence | landing page | replaced by the v3 scenes (see the top of this file) |
| ASCII spinner (braille frames) | loading / waking-up states | |

Reduced motion: no WebGL animation, no bob, no parallax, no decode, reveals shown at once,
the scroll sequence as static frames.

## New pieces

- Mark: a lens ring with crosshair ticks, chrome gradient (graphite in light mode).
  Real product terms, not decoration for its own sake.
- Removed 2026-11-11 (owner's call): the floating stickers and the landing page's limits section (limits now live in the README).
- ASCII dividers: `── 01 ── components ─────`.
- Empty state: a small ASCII field (`~ quiet ~`) next to the statement.
- Terminal block for code: always dark, in both modes.

## Budgets (unchanged targets)

Landing: Lighthouse mobile performance >= 85, accessibility >= 95, JS < 250 KB gzipped
(the shader is ~2 KB of inline code; no 3D library). App: no WebGL, so its bundle grows only
by the cursor and theme code.
