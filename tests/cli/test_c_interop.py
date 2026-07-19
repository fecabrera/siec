"""Tests for C-compatible struct passing to '@extern' functions.

Each test compiles real C with the system compiler and calls it from Sie,
so a wrong ABI lowering shows up as wrong values, not just wrong IR.
"""

import shutil
import subprocess

import pytest

from tests.cli.test_cli import run_cli

pytestmark = pytest.mark.skipif(shutil.which("cc") is None,
                                reason="needs a C compiler")

C_SOURCE = """
#include <stdint.h>
typedef struct { int32_t a, b; } small;
typedef struct { int64_t a, b; } pair64;
typedef struct { double x, y; } dpair;
typedef struct { int32_t a; double d; } mixed;
typedef struct { int64_t a, b, c; } big;
typedef union { int64_t i; double f; } pun;
int32_t sum_small(small s) { return s.a + s.b; }
int64_t sum_pair(pair64 p) { return p.a + p.b; }
double sum_dpair(dpair p) { return p.x + p.y; }
double sum_mixed(mixed m) { return (double)m.a + m.d; }
int64_t sum_big(big b) { return b.a + b.b + b.c; }
int64_t pun_bits(pun p) { return p.i; }
"""


def build_object(tmp_path):
    """
    Compile the C side into an object file.
    """
    (tmp_path / "lib.c").write_text(C_SOURCE)
    subprocess.run(["cc", "-c", str(tmp_path / "lib.c"),
                    "-o", str(tmp_path / "lib.o")], check=True)
    return tmp_path / "lib.o"


def test_structs_cross_into_c_by_value(tmp_path, monkeypatch):
    """
    Every ABI class round-trips: small ints, two eightbytes, float pairs,
    mixed int/float, larger-than-registers, and a union.
    """
    obj = build_object(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        struct small { a: i32; b: i32; }
        struct pair64 { a: i64; b: i64; }
        struct dpair { x: f64; y: f64; }
        struct mixed { a: i32; d: f64; }
        struct big { a: i64; b: i64; c: i64; }
        union pun { i: i64; f: f64; }

        @extern fn sum_small(s: small) -> i32;
        @extern fn sum_pair(p: pair64) -> i64;
        @extern fn sum_dpair(p: dpair) -> f64;
        @extern fn sum_mixed(m: mixed) -> f64;
        @extern fn sum_big(b: big) -> i64;
        @extern fn pun_bits(p: pun) -> i64;

        fn main() -> i32 {
            let s: small = { a = 3, b = 4 };
            let p: pair64 = { a = 5, b = 6 };
            let d: dpair = { x = 1.5, y = 2.5 };
            let m: mixed = { a = 2, d = 0.5 };
            let b: big = { a = 7, b = 8, c = 9 };
            let u: pun;
            u.f = 1.0;

            let total: i32 = sum_small(s)
                + sum_pair(p) as i32
                + sum_dpair(d) as i32
                + sum_mixed(m) as i32
                + sum_big(b) as i32;

            if (pun_bits(u) == 0x3FF0000000000000) {
                return total; // 7 + 11 + 4 + 2 + 24
            }
            return 1;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, obj, "--run") == 48


def test_sie_to_sie_calls_keep_their_convention(tmp_path, monkeypatch):
    """
    Only '@extern' callees reshape; Sie functions pass structs as before.
    """
    src = tmp_path / "main.sie"
    src.write_text("""
        struct pair { a: i32; b: i32; }

        fn sum(p: pair) -> i32 { return p.a + p.b; }

        fn main() -> i32 {
            let p: pair = { a = 40, b = 2 };
            return sum(p);
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42
