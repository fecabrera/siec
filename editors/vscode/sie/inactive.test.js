const assert = require("assert");
const { inactiveRanges } = require("./inactive");

assert.deepStrictEqual(inactiveRanges(new Uint32Array([
    1, 4, 15, 0, 0,
    1, 0, 20, 0, 0,
    0, 22, 3, 0, 0,
])), [
    [1, 4, 1, 19],
    [2, 0, 2, 20],
    [2, 22, 2, 25],
]);

assert.deepStrictEqual(inactiveRanges([]), []);
assert.deepStrictEqual(inactiveRanges([1, 2, 3]), []);

console.log("inactive-region tests passed");
