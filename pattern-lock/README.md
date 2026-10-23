# pattern-lock

Android-style 3×3 swipe-pattern login for single-user web apps. The browser sends the pattern to a server;
the server verifies it and only then sets a session cookie. Protected content lives on the server and is
never sent without that cookie (`401` otherwise).

```
pattern-lock.js              browser client (swipe grid, no dependencies)
server/pattern-auth.js       server core: scrypt-hashed pattern, signed tokens, lockout (Node, no dependencies)
server/server.js             example Node http server wiring it together
index.html                   demo app
```

## Run the demo

```
node pattern-lock/server/server.js      # http://localhost:8080
```

Starting pattern is `0 → 1 → 2 → 5 → 8` (dots numbered 0..8 row by row: top row, then down the right side).
It seeds the state file `server/.data/auth.json` on first start; after "Change pattern" the new one replaces it.

## Use in your app

Client:
```js
import { protect } from "./pattern-lock.js";
const gate = protect({ onUnlock: async () => { /* fetch protected data, render app */ } });
// gate.changePattern() -> Promise<boolean>;  gate.lock() -> logs out and reloads
```
Server (any Node handler):
```js
import { createAuth } from "./server/pattern-auth.js";
const auth = createAuth({ file: "/var/lib/myapp/auth.json" });
auth.login(pattern)        // { status: 200, token } | { status: 400|401|429, ... }
auth.verifyToken(token)    // true/false  -> guard every protected route with this
auth.changePattern(pattern)// call only after verifyToken; returns a fresh token, old ones die
```
`server.js` shows the routes: `POST /api/login`, `/api/logout`, `/api/pattern`, `GET /api/session`, `/api/content`.
Put your real data/pages behind `verifyToken`, as `/api/content` does.

## Security model

- Pattern is checked **only on the server**: HMAC-keyed with a server secret, then scrypt with a per-pattern salt, constant-time compare.
- A 3×3 grid has only ~389k valid patterns, so throttling is what protects it: 5 misses → 30 s lock, doubling per
  strike up to 1 h, global (single user), checked before hashing. The cost: someone can lock you out by guessing;
  the right pattern is also refused while locked.
- Session = signed, expiring (7 d) token in an `HttpOnly; SameSite=Strict` cookie. Changing the pattern invalidates all older tokens.
- POSTs must be `application/json` from the same origin (CSRF).
- **Set `PATTERN_AUTH_SECRET`** in the environment in production, so a leaked state file can't be brute-forced offline.
  Without it a random secret is stored in the state file. **Serve over HTTPS** (`COOKIE_SECURE=1`, or `TRUST_PROXY=1`
  behind a proxy that sets `x-forwarded-proto`); a pattern is easy to shoulder-surf, so treat this as a convenient lock, not a bank vault.
- Throttling is in the state file, so run a single server process.

Tests: `node pattern-lock/test.mjs` (client grid logic) and `node --test pattern-lock/server/pattern-auth.test.mjs`.
