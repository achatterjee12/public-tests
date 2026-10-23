// Server-side pattern verification. Framework-free; used by server.js but
// usable from any Node handler:  const auth = createAuth({ file });
//
// The pattern never lives in client code. The server keeps a salted scrypt hash
// (keyed with a server secret) and hands out a signed, expiring session token
// only for the right pattern. Wrong guesses are throttled globally with
// exponentially growing lockouts, since a 3x3 grid has only ~389k patterns.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export const GRID = 9;

export function isValidPattern(p, minLength = 4) {
  return Array.isArray(p) && p.length >= minLength && p.length <= GRID &&
    new Set(p).size === p.length && p.every((n) => Number.isInteger(n) && n >= 0 && n < GRID);
}

const b64 = (buf) => Buffer.from(buf).toString("base64url");
const eq = (a, b) => a.length === b.length && crypto.timingSafeEqual(a, b);

export function createAuth({
  file = null,                                  // JSON state file; null keeps state in memory (tests)
  secret = process.env.PATTERN_AUTH_SECRET,     // prefer env so a leaked state file can't be brute-forced
  defaultPattern = [0, 1, 2, 5, 8],             // used only to seed a brand-new state file
  minLength = 4,
  maxAttempts = 5,
  baseLockSeconds = 30,
  maxLockSeconds = 3600,
  tokenTtlSeconds = 7 * 24 * 3600,
  now = Date.now,
} = {}) {
  let state = null;
  if (file && fs.existsSync(file)) state = JSON.parse(fs.readFileSync(file, "utf8"));

  const key = () => Buffer.from(secret || state.secret, "utf8");
  const hashPattern = (pattern, salt) => {
    const keyed = crypto.createHmac("sha256", key()).update(pattern.join("-")).digest();
    return crypto.scryptSync(keyed, Buffer.from(salt, "base64url"), 32);
  };
  const save = () => {
    if (!file) return;
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const tmp = `${file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(state), { mode: 0o600 });
    fs.renameSync(tmp, file);
  };
  const setPattern = (pattern) => {
    const salt = b64(crypto.randomBytes(16));
    state.salt = salt;
    state.hash = b64(hashPattern(pattern, salt));
    state.version = (state.version || 0) + 1; // invalidates every older token
  };

  if (!state) {
    state = { secret: secret ? undefined : b64(crypto.randomBytes(32)), version: 0, fails: { count: 0, strikes: 0, until: 0 } };
    if (!isValidPattern(defaultPattern, minLength)) throw new Error("invalid defaultPattern");
    setPattern(defaultPattern);
    save();
  }

  const sign = (payload) => b64(crypto.createHmac("sha256", key()).update(payload).digest());
  function issueToken() {
    const payload = b64(JSON.stringify({ exp: now() + tokenTtlSeconds * 1000, v: state.version }));
    return `${payload}.${sign(payload)}`;
  }
  function verifyToken(token) {
    if (typeof token !== "string") return false;
    const [payload, sig, extra] = token.split(".");
    if (!payload || !sig || extra !== undefined) return false;
    if (!eq(Buffer.from(sig), Buffer.from(sign(payload)))) return false;
    try {
      const { exp, v } = JSON.parse(Buffer.from(payload, "base64url").toString());
      return exp > now() && v === state.version;
    } catch { return false; }
  }

  const retryAfter = () => Math.max(0, Math.ceil((state.fails.until - now()) / 1000));

  // -> { status: 200, token } | { status: 400|401|429, error, retryAfter? }
  function login(pattern) {
    if (!isValidPattern(pattern, minLength)) return { status: 400, error: "invalid pattern" };
    const wait = retryAfter();
    if (wait) return { status: 429, error: "locked", retryAfter: wait }; // checked before hashing: guesses cost nothing while locked
    const ok = eq(hashPattern(pattern, state.salt), Buffer.from(state.hash, "base64url"));
    const f = state.fails;
    if (ok) {
      state.fails = { count: 0, strikes: 0, until: 0 };
      save();
      return { status: 200, token: issueToken() };
    }
    f.count += 1;
    if (f.count >= maxAttempts) {
      f.strikes += 1;
      f.count = 0;
      f.until = now() + Math.min(baseLockSeconds * 2 ** (f.strikes - 1), maxLockSeconds) * 1000;
    }
    save();
    const left = retryAfter();
    return left
      ? { status: 429, error: "locked", retryAfter: left }
      : { status: 401, error: "wrong pattern", attemptsLeft: maxAttempts - f.count };
  }

  // Caller must have verified a token first. Returns a fresh token (old ones die).
  function changePattern(pattern) {
    if (!isValidPattern(pattern, minLength)) return { status: 400, error: "invalid pattern" };
    setPattern(pattern);
    save();
    return { status: 200, token: issueToken() };
  }

  return { login, verifyToken, changePattern, tokenTtlSeconds, minLength };
}
