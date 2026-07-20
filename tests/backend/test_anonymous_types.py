"""Feature tests for unnamed struct and union types."""

import pytest


def test_anonymous_union_field(run):
    """
    A struct field typed by an unnamed union shares storage like a named
    one: the tagged-value pattern.
    """
    result = run("""
        struct datum {
            type: i32;
            u: union {
                i: i64;
                f: f64;
                b: bool;
            };
        }

        fn main() -> i32 {
            let d: datum;
            d.type = 1;
            d.u.f = 1.0;
            if (d.u.i == 0x3FF0000000000000) { return 42; }
            return 1;
        }
    """)
    assert result.returncode == 42


def test_identical_shapes_are_one_type(run):
    """
    An unnamed type's identity is structural: the same fields spelled in
    an alias, a parameter, and in place all agree.
    """
    result = run("""
        @type point = struct { x: i32; y: i32; };

        fn dist2(p: struct { x: i32; y: i32; }) -> i32 {
            return p.x * p.x + p.y * p.y;
        }

        fn main() -> i32 {
            let p: point;
            p.x = 3;
            p.y = 4;
            return dist2(p) + (sizeof(point) as i32)
                 + (sizeof(struct { x: i32; y: i32; }) as i32) - 16;
        }
    """)
    assert result.returncode == 25


def test_anonymous_types_nest(run):
    """
    Unnamed structs and unions nest in each other and in raw arrays.
    """
    result = run("""
        struct packet {
            header: struct { kind: u8; len: u8; };
            body: union { words: @raw<u32>[4]; bytes: @raw<u8>[16]; };
        }

        fn main() -> i32 {
            let p: packet;
            p.header.kind = 7;
            p.body.words[0] = 0x29;
            return (p.body.bytes[0] + p.header.kind) as i32 - 6;
        }
    """)
    assert result.returncode == 42


def test_anonymous_local_and_positional_literal(run):
    """
    An unnamed struct types a local and takes a positional aggregate.
    """
    result = run("""
        fn main() -> i32 {
            let pair: struct { a: i32; b: i32; } = {40, 2};
            return pair.a + pair.b;
        }
    """)
    assert result.returncode == 42


def test_unnamed_members_hoist_their_fields(run):
    """
    An unnamed 'struct { ... };' or 'union { ... };' member hoists its
    fields into the enclosing struct, C-style, nesting included.
    """
    result = run("""
        struct packet {
            kind: u8;
            struct {
                x: i32;
                y: i32;
            };
            union {
                raw: u64;
                struct {
                    lo: u32;
                    hi: u32;
                };
            };
        }

        fn main() -> i32 {
            let p: packet;
            p.x = 3;
            p.y = 4;

            p.raw = 0x100000002;
            if (p.lo != 2 or p.hi != 1) { return 1; }

            return p.x + p.y - 7;
        }
    """)
    assert result.returncode == 0


def test_anonymous_union_literal_is_rejected(compile_source):
    """
    An unnamed union refuses aggregate literals like a named one.
    """
    with pytest.raises(TypeError, match="no aggregate literal"):
        compile_source("""
            fn main() -> i32 {
                let u: union { i: i64; f: f64; } = { i = 1 };
                return 0;
            }
        """)
