// pattern-lock (browser client): Android-style 3x3 swipe-pattern login.
// The pattern is sent to the server, which verifies it and sets a session
// cookie. Nothing secret lives here; see server/ for the other half.
//
//   import { protect } from "./pattern-lock.js";
//   protect({ onUnlock: async () => { /* fetch protected content */ } });
//
// Dots are numbered 0..8, left-to-right then top-to-bottom:
//   0 1 2
//   3 4 5
//   6 7 8

export const GRID = 3;
export const MIN_LENGTH = 4;
const DEFAULTS = {
  minLength: MIN_LENGTH,
  loginUrl: "/api/login",
  sessionUrl: "/api/session",
  patternUrl: "/api/pattern",
  logoutUrl: "/api/logout",
};

// ---- pure logic (no DOM; unit-tested in test.mjs) --------------------------

// Dots strictly between a and b on a straight line that are not already
// visited are swept up too, like Android (swiping 0 -> 2 also selects 1).
export function skippedBetween(a, b) {
  const [ar, ac] = [Math.floor(a / GRID), a % GRID];
  const [br, bc] = [Math.floor(b / GRID), b % GRID];
  const dr = br - ar, dc = bc - ac;
  if (dr % 2 !== 0 || dc % 2 !== 0) return null; // no dot sits exactly between
  if (dr === 0 && dc === 0) return null;
  return (ar + dr / 2) * GRID + (ac + dc / 2);
}

// Returns the dots to append when the finger reaches `next`, in order.
export function extendPath(path, next) {
  if (path.includes(next)) return [];
  if (!path.length) return [next];
  const mid = skippedBetween(path[path.length - 1], next);
  return mid !== null && !path.includes(mid) ? [mid, next] : [next];
}

export function isValidPattern(p, minLength = MIN_LENGTH) {
  return Array.isArray(p) && p.length >= minLength && p.length <= GRID * GRID &&
    new Set(p).size === p.length && p.every((n) => Number.isInteger(n) && n >= 0 && n < GRID * GRID);
}

export const samePattern = (a, b) => a.length === b.length && a.every((n, i) => n === b[i]);

// ---- UI widget --------------------------------------------------------------

const CSS = `
.plk{--plk-bg:#fff;--plk-fg:#1b1f27;--plk-muted:#667085;--plk-accent:#1d4ed8;--plk-bad:#d92d20;--plk-ok:#12b76a;
  position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;background:var(--plk-bg);color:var(--plk-fg);
  font:16px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding:16px;-webkit-user-select:none;user-select:none}
@media (prefers-color-scheme:dark){.plk{--plk-bg:#0f1218;--plk-fg:#e8eaef;--plk-muted:#98a1b3;--plk-accent:#7aa2ff;--plk-bad:#f97066;--plk-ok:#32d583}}
.plk-card{width:min(88vw,340px,70vh);text-align:center}
.plk h1{font-size:1.25rem;margin:0 0 6px}
.plk-msg{min-height:1.4em;margin:0 0 14px;color:var(--plk-muted)}
.plk-msg.bad{color:var(--plk-bad)}
.plk svg{display:block;width:100%;height:auto;touch-action:none;-webkit-tap-highlight-color:transparent;outline:none}
.plk .dot{fill:var(--plk-muted);opacity:.55;transition:fill .12s,opacity .12s}
.plk .ring{fill:none;stroke:var(--plk-accent);stroke-width:3;opacity:0;transition:opacity .12s}
.plk .on .dot{fill:var(--plk-accent);opacity:1}.plk .on .ring{opacity:.45}
.plk .line{stroke:var(--plk-accent);stroke-width:8;stroke-linecap:round;stroke-linejoin:round;fill:none;opacity:.7}
.plk .tail{stroke:var(--plk-accent);stroke-width:8;stroke-linecap:round;opacity:.4}
.plk.bad .line,.plk.bad .tail{stroke:var(--plk-bad)}.plk.bad .on .dot{fill:var(--plk-bad)}.plk.bad .ring{stroke:var(--plk-bad)}
.plk.ok .line{stroke:var(--plk-ok)}.plk.ok .on .dot{fill:var(--plk-ok)}.plk.ok .ring{stroke:var(--plk-ok)}
.plk-link{margin-top:14px;background:none;border:0;color:var(--plk-accent);font:inherit;cursor:pointer;padding:8px}
.plk-link[hidden]{display:none}
`;

function injectStyle() {
  if (document.getElementById("plk-style")) return;
  const s = document.createElement("style");
  s.id = "plk-style";
  s.textContent = CSS;
  document.head.append(s);
}

const SVGNS = "http://www.w3.org/2000/svg";
const el = (name, attrs = {}) => {
  const n = document.createElementNS(SVGNS, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const CELL = 100, HIT = 38;
const center = (i) => [(i % GRID) * CELL + CELL / 2, Math.floor(i / GRID) * CELL + CELL / 2];

// Draws the 3x3 grid into `host` and calls onComplete(pattern) when the finger
// lifts. Returns { flash(kind), reset(), disable(bool), destroy() }.
export function createGrid(host, { onComplete }) {
  const svg = el("svg", { viewBox: "0 0 300 300", role: "application", "aria-label": "3 by 3 pattern grid, draw with a finger swipe" });
  const line = el("polyline", { class: "line", points: "" });
  const tail = el("line", { class: "tail", opacity: 0 });
  svg.append(line, tail);
  const nodes = [];
  for (let i = 0; i < GRID * GRID; i++) {
    const [x, y] = center(i);
    const g = el("g");
    g.append(el("circle", { class: "ring", cx: x, cy: y, r: 30 }), el("circle", { class: "dot", cx: x, cy: y, r: 11 }));
    svg.append(g);
    nodes.push(g);
  }
  host.append(svg);

  let path = [], activeId = null, disabled = false, timer = 0;

  const draw = () => {
    nodes.forEach((g, i) => g.classList.toggle("on", path.includes(i)));
    line.setAttribute("points", path.map((i) => center(i).join(",")).join(" "));
  };
  const toLocal = (e) => {
    const r = svg.getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * 300, ((e.clientY - r.top) / r.height) * 300];
  };
  const hit = (x, y) => {
    for (let i = 0; i < GRID * GRID; i++) {
      const [cx, cy] = center(i);
      if (Math.hypot(x - cx, y - cy) <= HIT) return i;
    }
    return -1;
  };
  const moveTail = (x, y) => {
    const [lx, ly] = center(path[path.length - 1]);
    tail.setAttribute("x1", lx); tail.setAttribute("y1", ly);
    tail.setAttribute("x2", x); tail.setAttribute("y2", y);
    tail.setAttribute("opacity", 1);
  };
  const track = (e) => {
    if (e.pointerId !== activeId) return;
    const [x, y] = toLocal(e);
    const h = hit(x, y);
    if (h >= 0) { const add = extendPath(path, h); if (add.length) { path.push(...add); draw(); } }
    if (path.length) moveTail(x, y);
  };
  const onDown = (e) => {
    if (disabled || activeId !== null) return;
    clearTimeout(timer);
    reset();
    activeId = e.pointerId;
    svg.setPointerCapture?.(e.pointerId);
    e.preventDefault();
    track(e);
  };
  const onEnd = (e) => {
    if (e.pointerId !== activeId) return;
    activeId = null;
    tail.setAttribute("opacity", 0);
    if (path.length) onComplete([...path]);
  };

  function reset() { path = []; host.closest(".plk")?.classList.remove("bad", "ok"); draw(); }
  svg.addEventListener("pointerdown", onDown);
  svg.addEventListener("pointermove", track);
  svg.addEventListener("pointerup", onEnd);
  svg.addEventListener("pointercancel", onEnd);
  svg.addEventListener("contextmenu", (e) => e.preventDefault());

  return {
    // Show the drawn pattern in a state colour, then clear it.
    flash(kind, ms = 700) {
      host.closest(".plk")?.classList.add(kind);
      clearTimeout(timer);
      timer = setTimeout(reset, ms);
    },
    reset,
    disable(v) { disabled = v; svg.style.opacity = v ? 0.4 : 1; },
    destroy() { clearTimeout(timer); svg.remove(); },
  };
}

function overlay(title) {
  injectStyle();
  const root = document.createElement("div");
  root.className = "plk";
  root.innerHTML = `<div class="plk-card"><h1></h1><p class="plk-msg" role="status" aria-live="polite"></p><div class="plk-grid"></div>
    <button type="button" class="plk-link" hidden></button></div>`;
  root.querySelector("h1").textContent = title;
  const msg = root.querySelector(".plk-msg");
  return {
    root,
    host: root.querySelector(".plk-grid"),
    link: root.querySelector(".plk-link"),
    say(text, bad = false) { msg.textContent = text; msg.classList.toggle("bad", bad); },
  };
}

// ---- public API -------------------------------------------------------------

const post = (url, body) => fetch(url, {
  method: "POST", credentials: "same-origin",
  headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}),
});

// Shows the lock screen until the server accepts the pattern, then calls onUnlock.
// Returns { changePattern(), lock() }.
export function protect(options = {}) {
  const opts = { ...DEFAULTS, ...options };
  const mount = opts.mount || document.body;
  let unlocked = false;

  const unlock = () => { unlocked = true; opts.onUnlock?.(); };

  function showLock() {
    const ui = overlay(opts.title || "Draw your pattern");
    const grid = createGrid(ui.host, { onComplete: attempt });
    mount.append(ui.root);
    let tick = 0;

    const lockFor = (seconds) => {
      clearInterval(tick);
      const until = Date.now() + seconds * 1000;
      grid.disable(true);
      const update = () => {
        const s = Math.ceil((until - Date.now()) / 1000);
        if (s <= 0) { clearInterval(tick); grid.disable(false); ui.say("Try again"); return; }
        ui.say(`Too many attempts. Wait ${s}s.`, true);
      };
      update();
      tick = setInterval(update, 500);
    };

    async function attempt(pattern) {
      if (pattern.length < opts.minLength) {
        grid.flash("bad");
        return ui.say(`Connect at least ${opts.minLength} dots.`, true);
      }
      let res;
      try { res = await post(opts.loginUrl, { pattern }); }
      catch { grid.flash("bad"); return ui.say("Can't reach the server.", true); }
      if (res.ok) {
        grid.flash("ok", 250);
        return setTimeout(() => { clearInterval(tick); grid.destroy(); ui.root.remove(); unlock(); }, 250);
      }
      grid.flash("bad");
      if (res.status === 429) {
        const { retryAfter } = await res.json().catch(() => ({}));
        lockFor(retryAfter || 30);
      } else ui.say("Wrong pattern.", true);
    }
  }

  // Draw a new pattern twice to confirm. Only allowed once unlocked.
  function changePattern() {
    if (!unlocked) return Promise.reject(new Error("pattern-lock: unlock before changing the pattern"));
    return new Promise((resolve) => {
      const o = overlay("Set a new pattern");
      let first = null;
      const grid = createGrid(o.host, {
        async onComplete(p) {
          if (p.length < opts.minLength) { grid.flash("bad"); return o.say(`Connect at least ${opts.minLength} dots.`, true); }
          if (!first) { first = p; grid.flash("ok", 400); return o.say("Draw it again to confirm."); }
          if (!samePattern(first, p)) { first = null; grid.flash("bad"); return o.say("Didn't match. Start over.", true); }
          let res;
          try { res = await post(opts.patternUrl, { pattern: p }); }
          catch { first = null; grid.flash("bad"); return o.say("Can't reach the server.", true); }
          if (!res.ok) { first = null; grid.flash("bad"); return o.say("Server refused the new pattern.", true); }
          grid.destroy(); o.root.remove(); resolve(true);
        },
      });
      o.link.hidden = false;
      o.link.textContent = "Cancel";
      o.link.onclick = () => { grid.destroy(); o.root.remove(); resolve(false); };
      o.say(`Draw a pattern of at least ${opts.minLength} dots.`);
      mount.append(o.root);
    });
  }

  async function lock() {
    try { await post(opts.logoutUrl); } catch { /* cookie expires on its own */ }
    location.reload();
  }

  // Already have a valid session cookie? Skip the lock screen.
  fetch(opts.sessionUrl, { credentials: "same-origin" })
    .then((r) => (r.ok ? unlock() : showLock()))
    .catch(showLock);

  return { changePattern, lock };
}
