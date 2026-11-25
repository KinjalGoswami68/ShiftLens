// The four scenes: scroll drives the stage state (inspector.ts), a word-by-word reveal of
// each sentence, and the investigation note appearing word by word in scene 4.
// Smoothness rules (measured 2026-11-24, see DESIGN.md): the section is pinned with CSS sticky;
// one passive scroll listener, at most one update per frame, scrollY read once at the start of
// that frame (before anything is changed); every offset cached on load/resize; every reveal
// is an opacity change on spans built once. (GSAP ScrollTrigger was used before: its scroll
// reads after our class changes forced a style recalculation on every scroll frame.)
// Reduced motion: no scrubbing; each scene is a still frame, switched as it comes into view.
import type { Stage } from "./inspector";
import { noteSegments, type Scene } from "./data";

const clamp = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);
const smooth = (x: number) => { x = clamp(x); return x * x * (3 - 2 * x); };

function wordSpans(text: string, into: Node, cls = "w") {
  for (const part of text.split(/(\s+)/)) {
    if (!part) continue;
    if (/^\s+$/.test(part)) { into.appendChild(document.createTextNode(part)); continue; }
    const s = document.createElement("span"); s.className = cls; s.textContent = part; into.appendChild(s);
  }
}

/** Wraps each word of `el` in <span class="w"> (text stays in the DOM, in order, for screen readers). */
function splitWords(el: HTMLElement): HTMLElement[] {
  const walk = (node: Node) => {
    for (const ch of [...node.childNodes]) {
      if (ch.nodeType === Node.TEXT_NODE) { const frag = document.createDocumentFragment(); wordSpans(ch.textContent ?? "", frag); ch.replaceWith(frag); }
      else walk(ch);
    }
  };
  walk(el);
  return [...el.querySelectorAll<HTMLElement>(".w")];
}

/** The note as word spans and id chips, built once; revealed by count. */
function buildNote(box: HTMLElement, sr: HTMLElement, sc: Scene) {
  const segs = noteSegments(sc.d);
  box.textContent = "";
  for (const s of segs) {
    if (s.id) { const c = document.createElement("span"); c.className = "w cite"; c.textContent = s.text; box.appendChild(c); }
    else s.text.split("\n").forEach((line, i) => { if (i) box.appendChild(document.createElement("br")); wordSpans(line, box); });
  }
  sr.textContent = segs.map(s => s.text).join("");
  return [...box.querySelectorAll<HTMLElement>(".w")];
}

const docTop = (el: Element) => el.getBoundingClientRect().top + scrollY;

export function mount(stage: Stage, first: Scene, reduced: boolean) {
  const sec = document.getElementById("scenes")!, arts = [...sec.querySelectorAll<HTMLElement>(".scene")];
  const words = arts.map(a => splitWords(a.querySelector<HTMLElement>(".split")!));
  const body = document.getElementById("note-body")!, sr = document.getElementById("note-sr")!, meta = sec.querySelector<HTMLElement>("[data-ro='note-meta']")!;
  const stageEl = document.getElementById("stage")!, product = document.getElementById("product")!, stack = document.getElementById("stack");
  let noteWords: HTMLElement[] = [], shown = -1;
  const setScene = (sc: Scene) => {
    noteWords = buildNote(body, sr, sc); shown = -1;
    meta.textContent = `investigation note · incident #${sc.d.incident.id} · ${sc.d.note!.cause} · ${sc.d.note!.confidence.toFixed(2)}`;
    if (reduced) noteWords.forEach(w => w.classList.add("on"));
  };
  setScene(first);

  if (reduced) {
    const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) stage.setTarget(arts.indexOf(e.target as HTMLElement) + 1); }), { threshold: .5 });
    arts.forEach(a => io.observe(a));
    new IntersectionObserver(([e]) => { stageEl.style.opacity = e.isIntersecting || e.boundingClientRect.top > 0 ? "1" : "0"; }, { rootMargin: "0px 0px -40% 0px" })
      .observe(product);
    return { setScene };
  }

  sec.classList.add("js");
  // cached geometry (document coordinates); re-measured only on resize and when fonts settle
  let vh = 0, secTop = 0, len = 0, prodTop = 0, stackTop = 0, stackH = 0;
  const measure = () => {
    vh = innerHeight; secTop = docTop(sec); len = sec.offsetHeight; prodTop = docTop(product);
    if (stack) { stackTop = docTop(stack); stackH = stack.offsetHeight; }
  };
  let active = -1, stageOp = -1, stackP = -1;
  const lit = words.map(() => -1);
  const reveal = (ws: HTMLElement[], n: number, prev: number) => {
    if (n === prev) return;
    const [a, b] = n > prev ? [Math.max(0, prev), n] : [n, prev];
    for (let j = a; j < b && j < ws.length; j++) ws[j].classList.toggle("on", j < n);
  };
  const update = (y: number) => {
    const pos = y + vh - secTop;                          // 0 when the section's top meets the viewport bottom
    const e = clamp(pos / vh), p = clamp((pos - vh) / Math.max(1, len - vh));
    // state: 0 -> 1 while the section enters, then one change per scene (start of each quarter)
    let s = e;
    for (let k = 1; k <= 3; k++) s += smooth((p - (k * .25 - .02)) / .09);
    stage.setTarget(s);
    const a = e < 1 ? 0 : Math.min(3, Math.floor(p * 4));
    if (a !== active) { arts.forEach((el, i) => el.classList.toggle("on", i === a)); active = a; }
    words.forEach((ws, i) => {
      const q = i === 0 ? clamp((pos - vh * .35) / (vh * 1.1)) : clamp((p * 4 - i - .04) / .4);
      const n = Math.round(q * ws.length);
      reveal(ws, n, lit[i]); lit[i] = n;
    });
    const n = Math.round(noteWords.length * clamp((p * 4 - 3 - .15) / .55));
    reveal(noteWords, n, shown); shown = n;
    // the stage fades out as the product section arrives (top at the bottom -> top at 35%), then stops drawing
    const f = Math.round(clamp((y + vh - prodTop) / (vh * .65)) * 50) / 50;
    if (f !== stageOp) { stageEl.style.opacity = String(1 - f); stage.setVisible(f < 1); stageOp = f; }
    // product screens: tilted -> flat (transform and opacity only), until the stack's centre is at 55%
    if (stack) {
      const sp = Math.round(clamp((y + vh - stackTop) / (vh * .45 + stackH / 2)) * 200) / 200;
      if (sp !== stackP) { stack.style.setProperty("--p", String(sp)); stackP = sp; }
    }
  };
  let queued = false;
  const onScroll = () => {
    if (queued) return; queued = true;
    requestAnimationFrame(() => { queued = false; update(scrollY); });
  };
  measure(); update(scrollY);
  addEventListener("scroll", onScroll, { passive: true });
  // phones: the URL bar showing/hiding resizes the viewport mid-scroll; re-measure only on real size changes
  let lastW = innerWidth, lastH = innerHeight;
  addEventListener("resize", () => {
    if (innerWidth === lastW && Math.abs(innerHeight - lastH) < lastH * .15) return;
    lastW = innerWidth; lastH = innerHeight; measure(); update(scrollY);
  });
  document.fonts.ready.then(() => { measure(); update(scrollY); });

  return { setScene };
}
