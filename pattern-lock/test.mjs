// Run with: node pattern-lock/test.mjs
import assert from "node:assert/strict";
import { skippedBetween, extendPath, isValidPattern, samePattern } from "./pattern-lock.js";

assert.equal(skippedBetween(0, 2), 1);   // top row
assert.equal(skippedBetween(0, 8), 4);   // diagonal
assert.equal(skippedBetween(2, 6), 4);   // anti-diagonal
assert.equal(skippedBetween(1, 7), 4);   // column
assert.equal(skippedBetween(0, 1), null);
assert.equal(skippedBetween(0, 5), null); // knight move: nothing between
assert.equal(skippedBetween(3, 5), 4);

assert.deepEqual(extendPath([], 4), [4]);
assert.deepEqual(extendPath([0], 2), [1, 2]);       // sweeps up dot 1
assert.deepEqual(extendPath([1, 0], 2), [2]);       // 1 already used
assert.deepEqual(extendPath([0, 1], 0), []);        // no repeats
assert.deepEqual(extendPath([0], 8), [4, 8]);

assert.ok(isValidPattern([0, 1, 2, 5, 8]));
assert.ok(!isValidPattern([0, 1, 2]));              // too short
assert.ok(!isValidPattern([0, 1, 1, 2]));           // repeat
assert.ok(!isValidPattern([0, 1, 2, 9]));           // out of range
assert.ok(samePattern([1, 2], [1, 2]) && !samePattern([1, 2], [2, 1]));
console.log("ok");
