// Example server: static client + pattern login + a protected endpoint.
//   node pattern-lock/server/server.js        (PORT=8080, PATTERN_AUTH_SECRET=..., COOKIE_SECURE=1)
// Unauthenticated requests to protected routes get 401 and no content.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createAuth } from "./pattern-auth.js";

const COOKIE = "pl_session";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// Only these files are public; server/ and its state file never are.
const PUBLIC = { "/": "index.html", "/index.html": "index.html", "/pattern-lock.js": "pattern-lock.js" };
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8" };

export function createApp({ auth, protectedContent }) {
  const send = (res, status, body, headers = {}) => {
    const json = typeof body === "object";
    res.writeHead(status, {
      "content-type": json ? "application/json" : "text/plain; charset=utf-8",
      "cache-control": "no-store", "x-content-type-options": "nosniff", ...headers,
    });
    res.end(json ? JSON.stringify(body) : body);
  };
  const cookieOf = (req) => (req.headers.cookie || "").split(/;\s*/).find((c) => c.startsWith(COOKIE + "="))?.slice(COOKIE.length + 1);
  const secure = (req) => process.env.COOKIE_SECURE === "1" || req.socket.encrypted ||
    (process.env.TRUST_PROXY === "1" && req.headers["x-forwarded-proto"] === "https");
  const setCookie = (req, token, maxAge) =>
    ({ "set-cookie": `${COOKIE}=${token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${maxAge}${secure(req) ? "; Secure" : ""}` });
  const authed = (req) => auth.verifyToken(cookieOf(req));

  const readJson = (req) => new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (c) => { data += c; if (data.length > 1024) { reject(new Error("too large")); req.destroy(); } });
    req.on("end", () => { try { resolve(JSON.parse(data || "{}")); } catch (e) { reject(e); } });
  });

  return async (req, res) => {
    const url = new URL(req.url, "http://x").pathname;

    if (req.method === "GET" && PUBLIC[url]) {
      const f = PUBLIC[url];
      return send(res, 200, fs.readFileSync(path.join(root, f), "utf8"), { "content-type": TYPES[path.extname(f)] });
    }
    if (url === "/api/session" && req.method === "GET") {
      return authed(req) ? send(res, 200, { ok: true }) : send(res, 401, { error: "unauthorized" });
    }
    if (url === "/api/content" && req.method === "GET") {
      return authed(req) ? send(res, 200, protectedContent()) : send(res, 401, { error: "unauthorized" });
    }
    if (url.startsWith("/api/") && req.method === "POST") {
      // CSRF: JSON content-type forces a CORS preflight; also reject foreign Origins. Cookie is SameSite=Strict too.
      const origin = req.headers.origin;
      if (!/^application\/json/.test(req.headers["content-type"] || "") || (origin && new URL(origin).host !== req.headers.host)) {
        return send(res, 403, { error: "forbidden" });
      }
      if (url === "/api/logout") return send(res, 200, { ok: true }, setCookie(req, "", 0));
      let body;
      try { body = await readJson(req); } catch { return send(res, 400, { error: "bad request" }); }
      if (url === "/api/login") {
        const r = auth.login(body.pattern);
        if (r.status !== 200) return send(res, r.status, r, r.retryAfter ? { "retry-after": String(r.retryAfter) } : {});
        return send(res, 200, { ok: true }, setCookie(req, r.token, auth.tokenTtlSeconds));
      }
      if (url === "/api/pattern") {
        if (!authed(req)) return send(res, 401, { error: "unauthorized" });
        const r = auth.changePattern(body.pattern);
        if (r.status !== 200) return send(res, r.status, r);
        return send(res, 200, { ok: true }, setCookie(req, r.token, auth.tokenTtlSeconds));
      }
    }
    send(res, 404, { error: "not found" });
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const auth = createAuth({ file: process.env.AUTH_FILE || path.join(root, "server", ".data", "auth.json") });
  const app = createApp({
    auth,
    protectedContent: () => ({ html: "<p>This text only ever leaves the server for a logged-in session.</p>" }),
  });
  const port = Number(process.env.PORT) || 8080;
  http.createServer(app).listen(port, () => console.log(`pattern-lock demo on http://localhost:${port}`));
}
