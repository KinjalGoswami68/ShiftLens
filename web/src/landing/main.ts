import "../styles.css";
import "@fontsource/instrument-serif/latin-400.css";
import "@fontsource/instrument-serif/latin-400-italic.css";
import "./landing.css";
import { load, snapshotScene, type Scene } from "./data";
import { createStage, type Stage } from "./inspector";

const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const fine = matchMedia("(pointer: fine)").matches;
// no trackPointer() here: a root CSS variable per mouse move restyles the whole page and repaints the wordmark

// ---- the inspector: a still frame at load; motion starts on the first interaction ----
let liquid: { stop(): void } | null = null;
// safety net: on a device that keeps missing frames, the stage switches to its light mode
// (fewer points, pixel ratio 1, 30 fps) and calls this: the ripple and the sweeps go too.
const goLite = () => { liquid?.stop(); liquid = null; document.documentElement.classList.add("lite"); };
// Created at load, before the first paint. Deferring it to an idle callback was tried: the first
// GPU canvas setup (~100 ms, far more under software rendering) then counted as blocking time
// (Lighthouse TBT 68 -> 829 ms). Before the first paint it delays nothing a visitor sees.
const idle = (f: () => void, timeout = 600) => ("requestIdleCallback" in window ? requestIdleCallback(f, { timeout }) : setTimeout(f, 60));
let stage: Stage | null = null, scene = snapshotScene;
const ensureStage = (): Stage => {
  if (stage) return stage;
  stage = createStage(document.getElementById("stage-back") as HTMLCanvasElement, document.getElementById("stage-front") as HTMLCanvasElement,
    scene, { coarse: !fine, reduced, onSlow: goLite });
  stage.draw();
  document.getElementById("stage")!.classList.add("on");
  document.fonts.load("400 11px 'JetBrains Mono'").then(() => stage!.draw(), () => {});
  return stage;
};
ensureStage();

let scenes: { setScene(s: Scene): void } | null = null;
const evs = ["pointermove", "pointerdown", "touchstart", "scroll", "keydown", "wheel"] as const;
let started = false;
const start = () => {
  if (started) return; started = true;
  evs.forEach(ev => removeEventListener(ev, start));
  const stage = ensureStage();
  // liquid background (WebGL; never on software rendering or reduced motion). Laptops only: on phones it
  // added a touch handler and a WebGL redraw to every scroll frame while the points already move.
  if (!reduced && fine) {
    const canvas = document.getElementById("liquid") as HTMLCanvasElement;
    import("./liquid").then(m => { if (document.documentElement.classList.contains("lite")) return; liquid = m.startLiquid(canvas, !fine); if (liquid) canvas.style.background = ""; });
  }
  // scenes (GSAP ScrollTrigger) and live data
  // mounted when the main thread is idle, so building it never lands in the middle of a frame
  import("./scenes").then(m => idle(() => { scenes = m.mount(stage, scene, reduced); }));
  load().then(sc => {
    if (sc === scene) return;
    scene = sc; stage.setScene(sc); scenes?.setScene(sc);
  });
};
evs.forEach(ev => addEventListener(ev, start, { passive: true }));
const poke = () => stage?.poke();
for (const ev of ["pointermove", "pointerdown", "touchmove", "scroll", "keydown", "wheel"]) addEventListener(ev, poke, { passive: true });

{ // static gradient under everything until (unless) WebGL takes over
  const c = document.getElementById("liquid") as HTMLCanvasElement;
  c.style.background = "radial-gradient(1200px 700px at 72% 18%, #15161A, #07080A)";
}

// ---- shine sweep: pause while the hero is off screen ----
const hero = document.getElementById("hero");
if (hero && !reduced) new IntersectionObserver(([e]) => hero.classList.toggle("shine-off", !e.isIntersecting)).observe(hero);

// ---- tap pulse on phones ----
if (!fine && !reduced) addEventListener("pointerdown", e => {
  if (e.pointerType !== "touch") return;
  const el = (e.target as Element).closest?.(".tl, .copy") as HTMLElement | null;
  if (!el) return;
  el.classList.remove("tap"); void el.offsetWidth; el.classList.add("tap");
  setTimeout(() => el.classList.remove("tap"), 500);
}, { passive: true });

// ---- ASCII cursor (fine pointers) ----
if (fine) {
  const c = document.getElementById("cur")!, tag = c.querySelector(".tag")!;
  document.documentElement.classList.add("cur-on");
  let cx = innerWidth / 2, cy = innerHeight / 2, tx = cx, ty = cy, run = false;
  const loop = () => {
    cx += (tx - cx) * (reduced ? 1 : 0.35); cy += (ty - cy) * (reduced ? 1 : 0.35);
    c.style.transform = `translate3d(${cx}px,${cy}px,0)`;
    run = Math.abs(tx - cx) + Math.abs(ty - cy) > 0.3;
    if (run) requestAnimationFrame(loop);
  };
  addEventListener("pointermove", e => {
    tx = e.clientX; ty = e.clientY;
    if (!run) { run = true; requestAnimationFrame(loop); }
    const t = (e.target as Element).closest?.("a, button");
    c.classList.toggle("on-link", !!t);
    if (t) tag.textContent = t.tagName === "A" ? "[open]" : "[press]";
  }, { passive: true });
  addEventListener("pointerdown", () => c.classList.add("down"));
  addEventListener("pointerup", () => c.classList.remove("down"));
  document.addEventListener("pointerleave", () => { c.style.opacity = "0"; });
  document.addEventListener("pointerenter", () => { c.style.opacity = "1"; });
}

// ---- wordmark: ASCII decode once, desktop only (the real text is in the page from the start) ----
if (!reduced && fine && matchMedia("(min-width: 900px)").matches) {
  const parts = [...document.querySelectorAll<HTMLElement>(".brand-part")], glyphs = "#%&*+=-:./|<>[]01", t0 = performance.now();
  const finals = parts.map(p => p.textContent ?? "");
  parts.forEach(p => p.classList.remove("chrome-text"));
  const dec = (now: number) => {
    const pr = Math.min(1, (now - t0) / 700);
    parts.forEach((p, i) => {
      const f = finals[i], n = Math.floor(pr * f.length);
      let out = f.slice(0, n);
      for (let k = n; k < f.length; k++) out += glyphs[(Math.random() * glyphs.length) | 0];
      p.textContent = out;
    });
    if (pr < 1) requestAnimationFrame(dec);
    else parts.forEach((p, i) => { p.textContent = finals[i]; p.classList.add("chrome-text"); });
  };
  requestAnimationFrame(dec);
}

// ---- copy button ----
document.querySelectorAll<HTMLButtonElement>("[data-copy]").forEach(btn => {
  btn.addEventListener("click", async () => {
    const el = document.getElementById(btn.dataset.copy!);
    if (!el) return;
    try {
      await navigator.clipboard.writeText(el.innerText);
      btn.textContent = "copied";
      const live = btn.closest(".code")?.querySelector("[data-copied]");
      if (live) live.textContent = "Snippet copied";
      setTimeout(() => (btn.textContent = "copy"), 2000);
    } catch { /* clipboard blocked */ }
  });
});

// ---- demo video: nothing loads until the visitor clicks ----
const video = document.getElementById("demo-video") as HTMLVideoElement | null;
const play = document.getElementById("demo-play");
if (video && play) {
  play.addEventListener("click", () => { void video.play(); video.focus(); });
  video.addEventListener("play", () => play.remove(), { once: true });
}
