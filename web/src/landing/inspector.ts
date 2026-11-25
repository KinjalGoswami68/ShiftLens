// The inspector: a field of answers (points) with a lens drifting over them. One 2D canvas
// behind the text (points, trace) and one in front (lens, red answers, labels).
// States, interpolated by scroll: 0 hero loop (answers drift away, the lens marks them red),
// 1 normal (all settle into one cluster), 2 the trace (the real answers in order; the line
// turns red at the version marker), 3 one incident (the new-version answers gather into one
// red group under the lens), 4 the note (same, dimmed).
// Honest by construction: answer i < pts.length IS the i-th real answer; red = on the new
// version, never "over the threshold" (positions in the trace come from the real distances).
import type { Scene } from "./data";

const T = 12;          // hero loop, seconds
const STILL = 9.7;     // the still frame: lens over red answers, label shown
const IDLE_MS = 30000;

const smooth = (x: number) => { x = x < 0 ? 0 : x > 1 ? 1 : x; return x * x * (3 - 2 * x); };
const lerp = (a: number, b: number, f: number) => a + (b - a) * f;
function rng(seed: number) {
  return () => { seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
}
function gaussFrom(r: () => number) { let u = 0; while (!u) u = r(); const v = r(); const m = Math.sqrt(-2 * Math.log(u)); return [m * Math.cos(2 * Math.PI * v), m * Math.sin(2 * Math.PI * v)]; }

export interface Stage { setScene(s: Scene): void; setTarget(s: number): void; setVisible(v: boolean): void; poke(): void; draw(): void; lite(): void }

export function createStage(back: HTMLCanvasElement, front: HTMLCanvasElement, first: Scene, o: { coarse: boolean; reduced: boolean; onSlow?: () => void }): Stage {
  const bx = back.getContext("2d")!, fx = front.getContext("2d")!;
  const N = o.coarse ? 150 : 480;
  let nDraw = N, liteMode = false, fpsCap = o.coarse ? 30 : 60;
  const r0 = rng(7);
  const gx = new Float32Array(N), gy = new Float32Array(N), sz = new Float32Array(N), a0 = new Float32Array(N), ox = new Float32Array(N), oy = new Float32Array(N), dl = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    const [a, b] = gaussFrom(r0), [c, d] = gaussFrom(r0);
    gx[i] = a * .5; gy[i] = b * .5; ox[i] = c * .45; oy[i] = d * .45;
    sz[i] = .9 + r0() * 1.1; a0[i] = Math.max(.18, .9 - Math.hypot(gx[i], gy[i]) * .55); dl[i] = r0();
  }
  let sc = first, W = 0, H = 0, phone = false, pad = 48;
  let A = { c0x: 0, c0y: 0, c1x: 0, c1y: 0, c3x: 0, c3y: 0, sx: 0, sy: 0, ex: 0, ey: 0, lx: 0, ly: 0, lr: 0, span: 0, gxc: 0, gyc: 0, gr: 0, tx0: 0, tx1: 0, ty: 0, th: 0, lo: 0, hi: 1 };
  let DX = new Float32Array(0), DY = new Float32Array(0), TR = new Float32Array(0);   // drift targets + time each turns red
  let target = 0, s = 0, tt = STILL, running = false, last = 0, lastDraw = 0, lastInput = 0, visible = true, win: number[] = [];

  const nPts = () => Math.min(sc.pts.length, N);
  // new = on the new version (by each answer's own version, not by position: a late old-version answer stays silver)
  const isNew = (i: number) => i < nPts() && sc.pts[i][sc.version.field] === sc.version.value;
  const nNew = () => { let c = 0; for (let i = 0; i < nPts(); i++) if (isNew(i)) c++; return c; };
  const lensX = (u: number) => {
    const fwd = lerp(A.lx - A.span, A.lx + A.span * .18, smooth((u - 3) / 6.5));
    return u > 10.5 ? lerp(fwd, A.lx - A.span, smooth((u - 10.5) / 1.5)) : fwd;
  };
  const lensY = (u: number) => A.ly + Math.sin(u * .52) * 8;
  const dp = (i: number, u: number) => smooth((u - 1.2 - dl[i] * 2.2) / 2.6) * (1 - smooth((u - 10.5) / 1.5));
  const red0 = (k: number, u: number) => u >= TR[k] ? smooth((u - TR[k]) / .35) * (1 - smooth((u - 10.4) / .8)) : 0;
  const hull0 = (u: number) => smooth((u - 8.6) / .8) * (1 - smooth((u - 10.4) / .6));

  function layout() {
    // pixel ratio capped: the points are tiny, full retina resolution only costs fill rate
    const dpr = liteMode ? 1 : Math.min(devicePixelRatio || 1, o.coarse ? 1.25 : 1.5);
    W = innerWidth; H = innerHeight; phone = W < 640; pad = phone ? 16 : 48;
    for (const [c, x] of [[back, bx], [front, fx]] as const) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); x.setTransform(dpr, 0, 0, dpr, 0, 0); }
    const sx = phone ? 110 : Math.min(W * .18, 260), sy = phone ? 90 : Math.min(H * .17, 150);
    const c0x = W * (phone ? .42 : .56), c0y = H * (phone ? .30 : .36);
    const ds = sc.d.distance_series, ys = sc.pts.map(p => p.distance!);
    A = { c0x, c0y, sx, sy, c1x: W * (phone ? .55 : .64), c1y: H * (phone ? .30 : .34), c3x: W * (phone ? .36 : .50), c3y: H * (phone ? .30 : .36),
      ex: c0x + sx * .35, ey: c0y - sy * .1, lx: W * (phone ? .70 : .79), ly: H * (phone ? .25 : .25), lr: phone ? 50 : 84, span: phone ? 120 : 260,
      gxc: W * (phone ? .70 : .78), gyc: H * (phone ? .24 : .30), gr: phone ? 26 : 44,
      tx0: pad, tx1: W - pad, ty: H * (phone ? .13 : .15), th: H * (phone ? .27 : .34),
      lo: Math.min(...ys) - .03, hi: Math.max(ds.threshold ?? 0, ...ys) + .03 };
    // drift targets for the new-version answers, and when the lens first covers each one
    const r = rng(11), n = nPts();
    DX = new Float32Array(n); DY = new Float32Array(n); TR = new Float32Array(n).fill(Infinity);
    for (let i = sc.changeAt; i < n; i++) {
      if (!isNew(i)) continue;
      const f = .45 + .75 * r(), [a, b] = gaussFrom(r);
      DX[i] = A.ex + (A.lx - A.ex) * f + a * (10 + 22 * f); DY[i] = A.ey + (A.ly - A.ey) * f + b * (8 + 16 * f);
      for (let u = 3; u < 10.4; u += .05) if (dp(i, u) > .85 && Math.hypot(lensX(u) - DX[i], lensY(u) - DY[i]) < A.lr * .92) { TR[i] = u; break; }
    }
  }
  const traceX = (i: number) => lerp(A.tx0, A.tx1, i / Math.max(1, nPts() - 1));
  const traceY = (d: number) => A.ty + A.th * (1 - (d - A.lo) / (A.hi - A.lo));

  // position / alpha / red / size of point i in integer state k
  const P = { x: 0, y: 0, a: 0, red: 0, r: 0 };
  function at(k: number, i: number, clock: number) {
    const bx0 = gx[i] * A.sx, by0 = gy[i] * A.sy, breath = Math.sin(clock * .7 + i) * .8;
    const tr = i < nPts();
    P.r = sz[i]; P.red = 0; P.a = a0[i];
    if (k === 0) {
      P.x = A.c0x + bx0; P.y = A.c0y + by0 + breath;
      if (isNew(i)) { const f = dp(i, tt); P.x = lerp(P.x, DX[i], f); P.y = lerp(P.y, DY[i], f); P.red = red0(i, tt); P.a = Math.max(P.a, .65 * f); }
    } else if (k === 1) { P.x = A.c1x + bx0; P.y = A.c1y + by0 + breath; }
    else if (k === 2) {
      if (tr) { P.x = traceX(i); P.y = traceY(sc.pts[i].distance!); P.a = .95; P.r = isNew(i) ? 2.6 : 2.2; P.red = isNew(i) ? 1 : 0; }
      else { P.x = A.c1x + bx0 * .8; P.y = A.c1y + by0 * .8; P.a = a0[i] * .12; }
    } else {
      if (isNew(i)) { P.x = A.gxc + ox[i] * A.gr * 2; P.y = A.gyc + oy[i] * A.gr * 2; P.red = 1; P.a = .95; P.r = 2.2; }
      else { P.x = A.c3x + bx0; P.y = A.c3y + by0 + breath; }
      if (k === 4) P.a *= .1;
    }
  }

  function label(x: CanvasRenderingContext2D, txt: string, X: number, Y: number, color: string, alpha: number, align: "left" | "right" = "left") {
    if (alpha < .02) return;
    x.globalAlpha = alpha; x.font = "400 11px 'JetBrains Mono', ui-monospace, monospace"; x.textBaseline = "top";
    const w = x.measureText(txt).width, X0 = align === "right" ? X - w : X;
    x.fillStyle = "rgba(7,8,10,.85)"; x.fillRect(X0 - 5, Y - 3, w + 10, 17);
    x.fillStyle = color; x.fillText(txt, X0, Y); x.globalAlpha = 1;
  }
  function hull(x: CanvasRenderingContext2D, pts: [number, number][], grow: number, alpha: number) {
    if (pts.length < 3 || alpha < .02) return;
    const q = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const cr = (o: number[], a: number[], b: number[]) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lo: [number, number][] = [], up: [number, number][] = [];
    for (const p of q) { while (lo.length > 1 && cr(lo[lo.length - 2], lo[lo.length - 1], p) <= 0) lo.pop(); lo.push(p); }
    for (const p of q.reverse()) { while (up.length > 1 && cr(up[up.length - 2], up[up.length - 1], p) <= 0) up.pop(); up.push(p); }
    const h = lo.slice(0, -1).concat(up.slice(0, -1)), mx = h.reduce((s, p) => s + p[0], 0) / h.length, my = h.reduce((s, p) => s + p[1], 0) / h.length;
    x.globalAlpha = alpha; x.strokeStyle = "rgba(255,107,107,.75)"; x.lineWidth = 1; x.setLineDash([2, 3]); x.beginPath();
    h.forEach(([X, Y], i) => { const a = mx + (X - mx) * grow + Math.sign(X - mx) * 6, b = my + (Y - my) * grow + Math.sign(Y - my) * 6; i ? x.lineTo(a, b) : x.moveTo(a, b); });
    x.closePath(); x.stroke(); x.setLineDash([]); x.globalAlpha = 1;
  }
  function lens(x: CanvasRenderingContext2D, cx: number, cy: number, r: number, alpha: number) {
    if (alpha < .02) return;
    x.globalAlpha = alpha;
    const g = x.createRadialGradient(cx - r * .35, cy - r * .45, 0, cx, cy, r); g.addColorStop(0, "rgba(255,255,255,.10)"); g.addColorStop(1, "rgba(255,255,255,0)");
    x.fillStyle = g; x.beginPath(); x.arc(cx, cy, r, 0, 7); x.fill();
    x.strokeStyle = "rgba(236,238,241,.9)"; x.lineWidth = 1.2; x.beginPath(); x.arc(cx, cy, r, 0, 7); x.stroke();
    x.strokeStyle = "rgba(236,238,241,.32)"; x.lineWidth = 1; x.beginPath(); x.arc(cx, cy, r + 7, 0, 7); x.stroke();
    x.strokeStyle = "rgba(236,238,241,.8)"; x.lineWidth = 1.2;
    for (const [dx, dy] of [[0, -1], [0, 1], [-1, 0], [1, 0]]) { x.beginPath(); x.moveTo(cx + dx * (r - 6), cy + dy * (r - 6)); x.lineTo(cx + dx * (r + 14), cy + dy * (r + 14)); x.stroke(); }
    x.globalAlpha = 1;
  }

  const LV = 6;
  function draw() {
    const clock = performance.now() / 1000;
    bx.clearRect(0, 0, W, H); fx.clearRect(0, 0, W, H);
    const k0 = Math.floor(Math.min(4, Math.max(0, s))), k1 = Math.min(4, k0 + 1), f = smooth(s - k0);
    const w = (k: number) => Math.max(0, 1 - Math.abs(s - k));
    const buckets = Array.from({ length: LV }, () => new Path2D());
    const reds: [number, number, number][] = [], xs = new Float32Array(N), ys = new Float32Array(N);
    for (let i = 0; i < nDraw; i++) {
      at(k0, i, clock); const x0 = P.x, y0 = P.y, a_0 = P.a, r_0 = P.red, s_0 = P.r;
      if (f > 0) at(k1, i, clock);
      const x = lerp(x0, P.x, f), y = lerp(y0, P.y, f), a = lerp(a_0, P.a, f), red = lerp(r_0, P.red, f), rr = lerp(s_0, P.r, f);
      xs[i] = x; ys[i] = y;
      if (red > .02) reds.push([x, y, red * Math.min(1, a / .9)]);
      const sa = a * (1 - red);
      if (sa > .02) { const b = Math.min(LV - 1, Math.floor(sa * LV)); buckets[b].rect(x - rr / 2, y - rr / 2, rr, rr); }
    }
    buckets.forEach((p, b) => { bx.fillStyle = `rgba(214,218,224,${(b + .5) / LV})`; bx.fill(p); });

    // the trace (state 2): line through the real answers, silver then red at the version marker
    const w2 = w(2), n = nPts(), ca = sc.changeAt;
    if (w2 > .02) {
      bx.globalAlpha = w2; bx.lineWidth = 1;
      bx.strokeStyle = "rgba(214,218,224,.45)"; bx.beginPath(); for (let i = 0; i < ca; i++) i ? bx.lineTo(xs[i], ys[i]) : bx.moveTo(xs[i], ys[i]); bx.stroke();
      bx.strokeStyle = "rgba(255,107,107,.7)"; bx.beginPath(); for (let i = ca - 1; i < n; i++) i > ca - 1 ? bx.lineTo(xs[i], ys[i]) : bx.moveTo(xs[i], ys[i]); bx.stroke();
      const thr = sc.d.distance_series.threshold;
      if (thr != null) { const y = traceY(thr); bx.strokeStyle = "rgba(236,238,241,.45)"; bx.setLineDash([3, 4]); bx.beginPath(); bx.moveTo(A.tx0, y); bx.lineTo(A.tx1, y); bx.stroke(); bx.setLineDash([]);
        label(fx, `threshold ${thr.toFixed(3)}`, A.tx1 - 6, y + 6, "#A9ADB5", w2, "right"); }
      const mx = (traceX(ca - 1) + traceX(ca)) / 2;
      bx.strokeStyle = "rgba(255,107,107,.75)"; bx.beginPath(); bx.moveTo(mx, A.ty - 16); bx.lineTo(mx, A.ty + A.th + 8); bx.stroke();
      bx.globalAlpha = 1;
      label(fx, `${sc.label} · ${sc.date}`, mx + 8, A.ty - 18, "#FF6B6B", w2);
      label(fx, `last ${n} answers, in order →`, A.tx0, A.ty + A.th + 14, "#A9ADB5", w2);
      const prev = sc.pts[ca - 1][sc.version.field] ?? "before";
      label(fx, `${prev} ${n - nNew()} · ${sc.version.value} ${nNew()}`, A.tx1, A.ty + A.th + 14, "#A9ADB5", w2, "right");
    }

    // red answers in front, with a soft ring
    for (const [x, y, red] of reds) {
      fx.globalAlpha = red; fx.fillStyle = "#FF6B6B"; fx.beginPath(); fx.arc(x, y, 2.4, 0, 7); fx.fill();
      fx.strokeStyle = "rgba(255,107,107,.35)"; fx.beginPath(); fx.arc(x, y, 5, 0, 7); fx.stroke();
    }
    fx.globalAlpha = 1;

    // lens: hero path in state 0, over the red group in state 3
    const L = (k: number) => k === 0 ? [lensX(tt), lensY(tt), A.lr, 1] : k === 3 ? [A.gxc, A.gyc, Math.max(A.gr * 2.1, A.lr * .8), 1] : k === 4 ? [A.gxc, A.gyc, Math.max(A.gr * 2.1, A.lr * .8), 0] : [A.gxc, A.gyc, A.lr, 0];
    const la = L(k0), lb = L(k1);
    const lx = lerp(la[0], lb[0], f), ly = lerp(la[1], lb[1], f), lr = lerp(la[2], lb[2], f);
    lens(fx, lx, ly, lr, lerp(la[3], lb[3], f));

    // hero: dashed hull around the answers the lens marked, and the version label
    const w0 = w(0) * hull0(tt);
    if (w0 > .02) {
      hull(fx, reds.filter(r => r[2] > .5).map(([x, y]) => [x, y]), 1.3, w0);
      const ax = lx - lr * .72, ay = ly + lr * .72, bxx = ax - (phone ? 16 : 30), byy = ay + (phone ? 16 : 30);
      fx.globalAlpha = w0; fx.strokeStyle = "rgba(236,238,241,.6)"; fx.lineWidth = 1; fx.beginPath(); fx.moveTo(ax, ay); fx.lineTo(bxx, byy); fx.lineTo(bxx - 10, byy); fx.stroke(); fx.globalAlpha = 1;
      const txt = `${sc.label} · ${nNew()} answers`;
      label(fx, txt, bxx - 16, byy - 7, "#FF6B6B", w0, "right");
    }
    // state 3: one red group = one incident
    const w3 = w(3);
    if (w3 > .02) {
      hull(fx, reds.filter(r => r[2] > .5).map(([x, y]) => [x, y]), 1.25, w3);
      const nt = sc.d.note!;
      label(fx, `incident #${sc.d.incident.id} · ${nt.cause} · ${nt.confidence.toFixed(2)}`, W - pad, A.gyc + Math.max(A.gr * 2.1, A.lr * .8) + 24, "#ECEEF1", w3, "right");
    }
  }

  function frame(now: number) {
    if (!running) return;
    if (document.hidden || !visible) { running = false; return; }
    if (fpsCap < 60 && now - lastDraw < 1000 / fpsCap - 2) { requestAnimationFrame(frame); return; }
    const raw = now - last;
    const dt = Math.min(.05, raw / 1000 || .016); last = now; lastDraw = now;
    // safety net: if this device keeps missing frames, switch to the light mode once
    if (!liteMode && raw > 0 && raw < 1000) { win.push(raw); if (win.length >= 90) { const slow = win.filter(d => d > (fpsCap < 60 ? 50 : 34)).length; if (slow > win.length * .2) stage.lite(); win = []; } }
    tt = (tt + dt) % T;
    s += (target - s) * (1 - Math.pow(.88, dt * 60));
    draw();
    if (now - lastInput > IDLE_MS && Math.abs(target - s) < .001) { running = false; return; }
    requestAnimationFrame(frame);
  }
  const go = () => {
    lastInput = performance.now();
    if (o.reduced || running || document.hidden || !visible) return;
    running = true; last = lastInput; lastDraw = 0; requestAnimationFrame(frame);
  };
  const redraw = () => { if (!running) draw(); };

  layout();
  let lastW = W;
  addEventListener("resize", () => {
    // phones: the URL bar changes the height while scrolling; re-layout only on real size changes
    if (innerWidth === lastW && Math.abs(innerHeight - H) < H * .15) return;
    lastW = innerWidth; layout(); redraw();
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) redraw(); });

  const stage: Stage = {
    lite() {
      if (liteMode) return;
      liteMode = true; nDraw = Math.max(nPts(), Math.round(N * .5)); fpsCap = 30; layout(); redraw(); o.onSlow?.();
    },
    setScene(n) { sc = n; layout(); redraw(); },
    setTarget(v) { target = v; if (o.reduced) { s = v; redraw(); } else go(); },
    setVisible(v) { if (v === visible) return; visible = v; if (v) go(); },
    poke: go,
    draw,
  };
  return stage;
}
