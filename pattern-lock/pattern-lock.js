// pattern-lock: Android-style 3x3 swipe-pattern gate for single-user web apps.
//
//   import { protect } from "./pattern-lock.js";
//   protect({ mount: document.body, app: document.querySelector("#app") });
//
// Dots are numbered 0..8, left-to-right then top-to-bottom:
//   0 1 2
//   3 4 5
//   6 7 8
//
// NOTE: this runs entirely in the browser. It keeps a UI gate in front of the
// app and the stored pattern is hashed, but anything shipped to the client can
// be read by a determined visitor. Put truly secret data behind a server.

export const GRID = 3;
export const MIN_LENGTH = 4;
const DEFAULTS = {
  defaultPattern: [0, 1, 2, 5, 8], // used until the user changes the pattern
  minLength: MIN_LENGTH,
  storageKey: "pattern-lock",
  remember: "session", // "session" keeps you unlocked until the tab closes; "none" always asks
  maxAttempts: 5,
  lockoutSeconds: 30,
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

// ---- hashing & storage ------------------------------------------------------

const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

async function derive(pattern, saltHex) {
  if (!globalThis.crypto?.subtle) throw new Error("pattern-lock needs a secure context (https or localhost).");
  const salt = Uint8Array.from(saltHex.match(/../g).map((h) => parseInt(h, 16)));
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(pattern.join("-")), "PBKDF2", false, ["deriveBits"]);
  return hex(await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations: 150000 }, key, 256));
}

function makeStore(opts) {
  const k = opts.storageKey;
  const read = (area, name) => { try { return JSON.parse(area.getItem(`${k}:${name}`)); } catch { return null; } };
  const write = (area, name, v) => { try { area.setItem(`${k}:${name}`, JSON.stringify(v)); } catch { /* storage unavailable */ } };
  const clear = (area, name) => { try { area.removeItem(`${k}:${name}`); } catch { /* ignore */ } };
  return {
    credential: () => read(localStorage, "credential"),
    saveCredential: (c) => write(localStorage, "credential", c),
    fails: () => read(localStorage, "fails") || { count: 0, until: 0 },
    saveFails: (f) => write(localStorage, "fails", f),
    unlocked: () => read(sessionStorage, "unlocked") === true,
    setUnlocked: (v) => (v ? write(sessionStorage, "unlocked", true) : clear(sessionStorage, "unlocked")),
  };
}

async function verify(pattern, opts, store) {
  const cred = store.credential();
  if (!cred) return samePattern(pattern, opts.defaultPattern); // still on the hard-coded pattern
  return (await derive(pattern, cred.salt)) === cred.hash;
}

async function saveNewPattern(pattern, store) {
  const salt = hex(crypto.getRandomValues(new Uint8Array(16)));
  store.saveCredential({ salt, hash: await derive(pattern, salt) });
}

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

// Shows the lock screen over `mount` until the right pattern is drawn.
//   app:      element (or <template>) kept hidden until unlocked
//   onUnlock: called after a successful unlock
// Returns { changePattern(), lock(), whenUnlocked: Promise }.
export function protect(options = {}) {
  const opts = { ...DEFAULTS, ...options };
  if (!isValidPattern(opts.defaultPattern, opts.minLength)) throw new Error("pattern-lock: invalid defaultPattern");
  const mount = opts.mount || document.body;
  const store = makeStore(opts);
  const app = opts.app;
  let resolveUnlocked, ui = null;
  let whenUnlocked = new Promise((r) => (resolveUnlocked = r));
  let unlocked = false;

  if (app && !(app instanceof HTMLTemplateElement)) app.hidden = true;

  const reveal = () => {
    unlocked = true;
    if (app instanceof HTMLTemplateElement) app.replaceWith(app.content);
    else if (app) app.hidden = false;
    resolveUnlocked();
    opts.onUnlock?.();
  };

  function showLock() {
    ui = overlay(opts.title || "Draw your pattern");
    const grid = createGrid(ui.host, { onComplete: attempt });
    mount.append(ui.root);
    let tick = 0;

    const lockoutLeft = () => Math.max(0, Math.ceil((store.fails().until - Date.now()) / 1000));
    const refreshLockout = () => {
      clearInterval(tick);
      if (!lockoutLeft()) { grid.disable(false); ui.say(""); return; }
      grid.disable(true);
      const update = () => {
        const s = lockoutLeft();
        if (!s) { clearInterval(tick); grid.disable(false); ui.say("Try again"); return; }
        ui.say(`Too many attempts. Wait ${s}s.`, true);
      };
      update();
      tick = setInterval(update, 500);
    };

    async function attempt(pattern) {
      if (lockoutLeft()) return grid.reset();
      if (pattern.length < opts.minLength) {
        grid.flash("bad");
        return ui.say(`Connect at least ${opts.minLength} dots.`, true);
      }
      let ok = false;
      try { ok = await verify(pattern, opts, store); }
      catch (err) { grid.flash("bad"); return ui.say(err.message, true); }
      if (ok) {
        store.saveFails({ count: 0, until: 0 });
        if (opts.remember === "session") store.setUnlocked(true);
        grid.flash("ok", 250);
        setTimeout(() => { clearInterval(tick); grid.destroy(); ui.root.remove(); ui = null; reveal(); }, 250);
      } else {
        const f = store.fails();
        const count = f.count + 1;
        const locked = count >= opts.maxAttempts;
        store.saveFails({ count: locked ? 0 : count, until: locked ? Date.now() + opts.lockoutSeconds * 1000 : 0 });
        grid.flash("bad");
        if (locked) refreshLockout(); else ui.say("Wrong pattern.", true);
      }
    }
    refreshLockout();
  }

  // Draw a new pattern twice to confirm. Only allowed once unlocked.
  async function changePattern() {
    if (!unlocked) throw new Error("pattern-lock: unlock before changing the pattern");
    return new Promise((resolve) => {
      const o = overlay("Set a new pattern");
      let first = null;
      const grid = createGrid(o.host, {
        async onComplete(p) {
          if (p.length < opts.minLength) { grid.flash("bad"); return o.say(`Connect at least ${opts.minLength} dots.`, true); }
          if (!first) { first = p; grid.flash("ok", 400); return o.say("Draw it again to confirm."); }
          if (!samePattern(first, p)) { first = null; grid.flash("bad"); return o.say("Didn't match. Start over.", true); }
          await saveNewPattern(p, store);
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

  function lock() {
    store.setUnlocked(false);
    location.reload(); // simplest way to guarantee nothing from the app stays in memory
  }

  if (opts.remember === "session" && store.unlocked()) reveal();
  else showLock();

  return { changePattern, lock, get whenUnlocked() { return whenUnlocked; } };
}
