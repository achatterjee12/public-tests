// node --test pattern-lock/server
import test from "node:test";
import assert from "node:assert/strict";
import { createAuth } from "./pattern-auth.js";

const mk = (extra = {}) => {
  const clock = { t: 1_000_000 };
  return { clock, auth: createAuth({ secret: "s3cret", now: () => clock.t, ...extra }) };
};
const OK = [0, 1, 2, 5, 8], BAD = [0, 3, 6, 7];

test("correct pattern issues a verifiable token; wrong does not", () => {
  const { auth } = mk();
  const bad = auth.login(BAD);
  assert.equal(bad.status, 401);
  assert.equal(bad.token, undefined);
  const ok = auth.login(OK);
  assert.equal(ok.status, 200);
  assert.ok(auth.verifyToken(ok.token));
});

test("rejects malformed patterns and forged/tampered tokens", () => {
  const { auth } = mk();
  for (const p of [null, "01258", [0, 1], [0, 0, 1, 2], [0, 1, 2, 9], {}]) assert.equal(auth.login(p).status, 400);
  const { token } = auth.login(OK);
  const [payload, sig] = token.split(".");
  const forged = Buffer.from(JSON.stringify({ exp: 9e15, v: 1 })).toString("base64url");
  assert.ok(!auth.verifyToken(`${forged}.${sig}`));
  assert.ok(!auth.verifyToken(`${payload}.x${sig}`));
  assert.ok(!auth.verifyToken(undefined));
  assert.ok(!auth.verifyToken("garbage"));
});

test("tokens expire", () => {
  const { auth, clock } = mk({ tokenTtlSeconds: 60 });
  const { token } = auth.login(OK);
  clock.t += 59_000; assert.ok(auth.verifyToken(token));
  clock.t += 2_000;  assert.ok(!auth.verifyToken(token));
});

test("lockout after 5 misses doubles each time and blocks even the right pattern", () => {
  const { auth, clock } = mk();
  for (let i = 0; i < 4; i++) assert.equal(auth.login(BAD).status, 401);
  const locked = auth.login(BAD);
  assert.equal(locked.status, 429);
  assert.equal(locked.retryAfter, 30);
  assert.equal(auth.login(OK).status, 429);          // correct guess during lockout still refused
  clock.t += 31_000;
  for (let i = 0; i < 4; i++) auth.login(BAD);
  assert.equal(auth.login(BAD).retryAfter, 60);       // second strike: doubled
  clock.t += 61_000;
  assert.equal(auth.login(OK).status, 200);           // success resets
  for (let i = 0; i < 4; i++) auth.login(BAD);
  assert.equal(auth.login(BAD).retryAfter, 30);
});

test("changePattern swaps the secret and kills older tokens", () => {
  const { auth } = mk();
  const old = auth.login(OK).token;
  const fresh = auth.changePattern([6, 7, 8, 5]).token;
  assert.ok(!auth.verifyToken(old));
  assert.ok(auth.verifyToken(fresh));
  assert.equal(auth.login(OK).status, 401);
  assert.equal(auth.login([6, 7, 8, 5]).status, 200);
});
