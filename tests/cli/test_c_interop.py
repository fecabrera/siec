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


RETURN_C_SOURCE = """
#include <stdint.h>
typedef struct { int32_t a, b; } small;
typedef struct { double x, y; } dpair;
typedef struct { int32_t a; double d; } mixed;
typedef struct { int64_t a, b, c; } big;
small make_small(int32_t a, int32_t b) { return (small){a, b}; }
dpair make_dpair(double x, double y) { return (dpair){x, y}; }
mixed make_mixed(int32_t a, double d) { return (mixed){a, d}; }
big make_big(int64_t a, int64_t b, int64_t c) { return (big){a, b, c}; }
big round_trip(big b) { b.c += 1; return b; }
"""


def test_structs_return_from_c_by_value(tmp_path, monkeypatch):
    """
    Struct returns come back the C way: registers for the small classes,
    the hidden 'sret' slot for the large, mixing with lowered arguments.
    """
    (tmp_path / "ret.c").write_text(RETURN_C_SOURCE)
    obj = tmp_path / "ret.o"
    subprocess.run(["cc", "-c", str(tmp_path / "ret.c"), "-o", str(obj)],
                   check=True)

    src = tmp_path / "main.sie"
    src.write_text("""
        struct small { a: i32; b: i32; }
        struct dpair { x: f64; y: f64; }
        struct mixed { a: i32; d: f64; }
        struct big { a: i64; b: i64; c: i64; }

        @extern fn make_small(a: i32, b: i32) -> small;
        @extern fn make_dpair(x: f64, y: f64) -> dpair;
        @extern fn make_mixed(a: i32, d: f64) -> mixed;
        @extern fn make_big(a: i64, b: i64, c: i64) -> big;
        @extern fn round_trip(b: big) -> big;

        fn main() -> i32 {
            let s = make_small(3, 4);
            let d = make_dpair(1.5, 2.5);
            let m = make_mixed(2, 0.5);
            let b = round_trip(make_big(7, 8, 9));

            let total: i32 = s.a + s.b
                + (d.x + d.y) as i32
                + ((m.a as f64) + m.d) as i32
                + (b.a + b.b + b.c) as i32;

            return total; // 7 + 4 + 2 + 25
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, obj, "--run") == 38


def test_libc_div_returns_its_struct(tmp_path, monkeypatch):
    """
    libc's div() returns a real struct: quotient and remainder arrive intact.
    """
    src = tmp_path / "main.sie"
    src.write_text("""
        struct div_t { quot: i32; rem: i32; }

        @extern fn div(numer: i32, denom: i32) -> div_t;

        fn main() -> i32 {
            let r = div(87, 2);
            return r.quot - r.rem; // 43 - 1
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42


@pytest.mark.skipif(shutil.which("ar") is None, reason="needs ar")
def test_static_libraries_link_and_jit(tmp_path, monkeypatch):
    """
    A '.a' on the command line joins the build: the linker takes it as
    is, and '--run' unpacks its members into the JIT.
    """
    (tmp_path / "a.c").write_text("int forty(void) { return 40; }\n")
    (tmp_path / "b.c").write_text("int two(void) { return 2; }\n")
    subprocess.run(["cc", "-c", str(tmp_path / "a.c"), "-o", str(tmp_path / "a.o")],
                   check=True)
    subprocess.run(["cc", "-c", str(tmp_path / "b.c"), "-o", str(tmp_path / "b.o")],
                   check=True)
    subprocess.run(["ar", "rcs", str(tmp_path / "libnums.a"),
                    str(tmp_path / "a.o"), str(tmp_path / "b.o")], check=True)

    src = tmp_path / "main.sie"
    src.write_text("""
        @extern fn forty() -> i32;
        @extern fn two() -> i32;

        fn main() -> i32 { return forty() + two(); }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, tmp_path / "libnums.a", "--run") == 42

    assert run_cli(monkeypatch, src, tmp_path / "libnums.a",
                   "-o", str(tmp_path / "prog")) == 0
    result = subprocess.run([str(tmp_path / "prog")])
    assert result.returncode == 42


UNION_C_SOURCE = """
#include <stdint.h>
typedef struct {
  int32_t type;
  union {
    const char *s;
    struct { const char *ptr; int32_t len; } str;
    struct { int32_t size; const char **key; int32_t *len; void *value; } tab;
  } u;
} datum;
datum make_str(const char *s, int32_t len) {
  datum d; d.type = 1; d.u.str.ptr = s; d.u.str.len = len; return d;
}
"""


def test_padded_union_survives_the_return_copy(tmp_path, monkeypatch):
    """
    A union led by a padded struct keeps every byte through a C return:
    the padding inside one member is live data of another (tomlc17's
    'toml_datum', whose string pointer lost its high half).
    """
    (tmp_path / "u.c").write_text(UNION_C_SOURCE)
    obj = tmp_path / "u.o"
    subprocess.run(["cc", "-c", str(tmp_path / "u.c"), "-o", str(obj)],
                   check=True)

    src = tmp_path / "main.sie"
    src.write_text("""
        struct datum {
            type: i32;
            u: union {
                s: const char*;
                str: struct { ptr: const char*; len: i32; };
                tab: struct { size: i32; key: const char**; len: i32*; value: opaque*; };
            };
        }

        @extern fn make_str(s: const char*, len: i32) -> datum;
        @extern fn strcmp(a: const char*, b: const char*) -> i32;

        fn main() -> i32 {
            let d = make_str("hello", 5);
            if (d.type == 1 and strcmp(d.u.s, "hello") == 0) {
                return d.u.str.len; // 5
            }
            return 100;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, obj, "--run") == 5
