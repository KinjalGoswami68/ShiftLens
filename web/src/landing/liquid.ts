// Liquid chrome background: one fragment shader at low resolution behind the whole page.
// It renders only while something happens (pointer, touch, scroll, theme, intensity, resize),
// keeps flowing for IDLE_MS after the last interaction, then stops and draws nothing.
// Phones (coarse pointer): smaller canvas, device pixel ratio capped at 1, at most 30 fps.
import { THEME_EVENT, currentIntensity, isLight } from "../theme";

const VS = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
// The output is clamped so text on the bare background keeps WCAG AA (4.5:1) on every pixel:
// dark mode <= 0.155 (subtle text #8E929B stays >= 4.6:1), light mode >= 0.83 (subtle #575B63 >= 4.6:1).
const FS = `precision mediump float;
uniform vec2 res,mouse;uniform float t,amp,inten,light,scroll;
float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y);}
float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<4;i++){v+=a*n(p);p*=2.03;a*=.5;}return v;}
float field(vec2 uv){float d=distance(uv,mouse);float ripple=amp*exp(-d*4.)*sin(d*34.-t*5.);
 return fbm(uv*vec2(.85,1.35)+vec2(t*.01,t*.03+scroll*.35))*.9+ripple*.26+(1.-d)*.12;}
void main(){vec2 uv=gl_FragCoord.xy/res;uv.x*=res.x/res.y;
 float e=2./res.y;float c=field(uv);vec3 nr=normalize(vec3(field(uv+vec2(e,0.))-c,field(uv+vec2(0.,e))-c,e*1.4));
 float band=.5+.5*sin(nr.y*5.+nr.x*2.5+c*4.);
 float spec=pow(max(0.,dot(nr,normalize(vec3(mouse.x-uv.x,mouse.y-uv.y,.9)))),24.);
 float metal=band*.55+spec*.9;
 vec3 dark=vec3(.02,.022,.026)+vec3(.11,.115,.125)*metal*(.3+inten*.6)+vec3(.30,.55,.75)*spec*.06*inten;
 vec3 lite=vec3(.85,.855,.85)+vec3(.12)*inten-vec3(.09,.09,.085)*(1.-metal)*(.4+.3*(1.-inten));
 gl_FragColor=vec4(mix(min(dark,vec3(.155)),max(lite,vec3(.83)),light),1.);}`;

const IDLE_MS = 4000;

export function startLiquid(cv: HTMLCanvasElement, coarse: boolean): boolean {
  const g = cv.getContext("webgl", { antialias: false, alpha: false, powerPreference: "low-power", failIfMajorPerformanceCaveat: true });
  if (!g) return false;
  // No GPU (software rasteriser): the shader would block the main thread; keep the static background.
  const dbg = g.getExtension("WEBGL_debug_renderer_info");
  const renderer = String(dbg ? g.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : g.getParameter(g.RENDERER));
  if (/swiftshader|llvmpipe|software|basic render|mesa offscreen/i.test(renderer)) return false;
  const sh = (type: number, src: string) => { const s = g.createShader(type)!; g.shaderSource(s, src); g.compileShader(s); return s; };
  const pr = g.createProgram()!;
  g.attachShader(pr, sh(g.VERTEX_SHADER, VS)); g.attachShader(pr, sh(g.FRAGMENT_SHADER, FS)); g.linkProgram(pr);
  if (!g.getProgramParameter(pr, g.LINK_STATUS)) return false;
  g.useProgram(pr);
  g.bindBuffer(g.ARRAY_BUFFER, g.createBuffer());
  g.bufferData(g.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), g.STATIC_DRAW);
  const loc = g.getAttribLocation(pr, "p"); g.enableVertexAttribArray(loc); g.vertexAttribPointer(loc, 2, g.FLOAT, false, 0, 0);
  const U = Object.fromEntries(["res", "mouse", "t", "amp", "inten", "light", "scroll"].map(k => [k, g.getUniformLocation(pr, k)]));

  // canvas pixels per CSS pixel: 0.4 on desktop (as before); phones cap DPR at 1, then 0.32
  const scale = coarse ? Math.min(devicePixelRatio || 1, 1) * 0.32 : 0.4;
  const minFrame = coarse ? 1000 / 30 - 2 : 0;
  let amp = 0, t = 0, last = 0, drawn = 0, running = false, lastInput = 0, sy = scrollY, scrollF = sy / innerHeight;
  const m = [0.7, 0.6], target = [0.7, 0.6];
  let w = 0, h = 0;
  const size = () => {
    // phones: the URL bar showing/hiding changes the height while scrolling; keep the buffer
    w = innerWidth; if (!coarse || Math.abs(innerHeight - h) > h * 0.2 || !h) h = innerHeight;
    cv.width = Math.ceil(w * scale); cv.height = Math.ceil(h * scale);
    g.viewport(0, 0, cv.width, cv.height);
  };
  const render = () => {
    g.uniform2f(U.res, cv.width, cv.height); g.uniform2f(U.mouse, m[0] * cv.width / cv.height, m[1]);
    g.uniform1f(U.t, t); g.uniform1f(U.amp, amp); g.uniform1f(U.inten, currentIntensity()); g.uniform1f(U.light, isLight() ? 1 : 0);
    g.uniform1f(U.scroll, scrollF);
    g.drawArrays(g.TRIANGLE_STRIP, 0, 4);
  };
  const tick = (now: number) => {
    if (document.hidden) { running = false; return; }
    if (now - drawn < minFrame) { requestAnimationFrame(tick); return; }
    const dt = Math.min(0.05, (now - last) / 1000 || 0.016); last = now; drawn = now;
    t += dt;
    const k = 1 - Math.pow(0.92, dt * 60), decay = Math.pow(0.965, dt * 60);
    m[0] += (target[0] - m[0]) * k; m[1] += (target[1] - m[1]) * k; amp *= decay;
    scrollF += (sy / innerHeight - scrollF) * k;
    render();
    const moving = amp > 0.01 || Math.abs(target[0] - m[0]) + Math.abs(target[1] - m[1]) > 0.001 || Math.abs(sy / innerHeight - scrollF) > 0.001;
    if (moving || now - lastInput < IDLE_MS) requestAnimationFrame(tick);
    else running = false;
  };
  const go = () => {
    lastInput = performance.now();
    if (!running && !document.hidden) { running = true; last = lastInput; drawn = 0; requestAnimationFrame(tick); }
  };
  const poke = (x: number, y: number, energy: number) => {
    target[0] = x / innerWidth; target[1] = 1 - y / innerHeight;
    amp = Math.min(1, amp + energy);
    go();
  };

  size(); render();
  addEventListener("resize", () => { size(); render(); });
  addEventListener(THEME_EVENT, render);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) render(); });
  let px = -1, py = -1;
  addEventListener("pointermove", e => {
    const dx = px < 0 ? 0 : e.clientX - px, dy = py < 0 ? 0 : e.clientY - py;
    px = e.clientX; py = e.clientY;
    poke(e.clientX, e.clientY, Math.hypot(dx, dy) / 120);
  }, { passive: true });
  addEventListener("pointerdown", e => { px = e.clientX; py = e.clientY; poke(e.clientX, e.clientY, 0.6); }, { passive: true });
  // touchmove keeps firing while the page scrolls under the finger (pointermove is cancelled then)
  if (coarse) addEventListener("touchmove", e => {
    const p = e.touches[0]; if (!p) return;
    const dx = p.clientX - px, dy = p.clientY - py; px = p.clientX; py = p.clientY;
    poke(p.clientX, p.clientY, Math.min(0.25, Math.hypot(dx, dy) / 160));
  }, { passive: true });
  addEventListener("scroll", () => {
    const d = scrollY - sy; sy = scrollY;
    amp = Math.min(1, amp + Math.min(0.2, Math.abs(d) / 900));
    go();
  }, { passive: true });
  return true;
}
