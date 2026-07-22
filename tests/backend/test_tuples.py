"""Feature tests for the builtin 'Tuple<A, B, ...>' type."""

import pytest


def test_tuples_build_and_index(run):
    """
    'Tuple<A, B, ...>' declares per arity; '(a, b, ...)' builds one,
    coerced by an annotation or inferred from its elements; elements
    read and write by constant index, and '.length' is the arity.
    """
    source = """
    fn swap(t: Tuple<i32, i32>) -> Tuple<i32, i32> {
        return (t[1], t[0]);
    }

    struct Slot { pair: Tuple<u8, f64>; }

    fn main() -> i32 {
        let t: Tuple<i32, f64>;
        t[0] = 40;
        t[1] = 2.5;
        if (t[0] != 40) { return 1; }

        let p = (1, 2.5, "three");           // inferred element types
        if (p[0] != 1 or p[2][0] != 't') { return 2; }
        if (p.length != 3) { return 3; }

        let q: Tuple<u8, i64> = (7, 9);      // coerced by the annotation
        if (q[0] != 7 or q[1] != 9) { return 4; }

        let s = swap((1, 41));               // by value and returned
        if (s[0] != 41 or s[1] != 1) { return 5; }

        let n: Tuple<Tuple<i32, i32>, i32> = ((1, 2), 3);
        if (n[0][1] != 2 or n[2 - 1] != 3) { return 6; }

        let slot: Slot = { (5, 1.5) };       // a struct field
        if (slot.pair[0] != 5) { return 7; }

        let one = (42,);                     // the single-element spelling
        if (one[0] != 42 or one.length != 1) { return 8; }

        return (sizeof(Tuple<i32, i32>) as i32) - 8;
    }
    """
    assert run(source).returncode == 0


def test_tuple_indices_are_constant_and_bounded(compile_source):
    """
    A tuple index must be a compile-time constant inside the arity, and
    '.length' is read-only.
    """
    with pytest.raises(TypeError, match="a tuple index must be a "
                                        "compile-time constant"):
        compile_source("""
        fn main() -> i32 { let t = (1, 2); let i = 1; return t[i]; }
        """)

    with pytest.raises(TypeError, match="tuple index 5 is out of range "
                                        "for 'Tuple<i32,i32>'"):
        compile_source("""
        fn main() -> i32 { let t = (1, 2); return t[5]; }
        """)

    with pytest.raises(TypeError, match="struct has no field 'length'"):
        compile_source("""
        fn main() -> i32 { let t = (1, 2); t.length = 5; return 0; }
        """)


def test_tuple_destructuring(run):
    """
    'let (a, b) = pair;' binds each element to a fresh local copy;
    patterns nest, and a trailing comma spells the single-name pattern.
    """
    source = """
    fn minmax(a: i32, b: i32) -> Tuple<i32, i32> {
        return a < b ? (a, b) : (b, a);
    }

    fn main() -> i32 {
        let (lo, hi) = minmax(9, 3);
        if (lo != 3 or hi != 9) { return 1; }

        let (n, f, s) = (1, 2.5, "three");
        if (n != 1 or f != 2.5 or s[0] != 't') { return 2; }

        let ((a, b), c) = ((10, 20), 30);
        if (a + b + c != 60) { return 3; }

        let pair = (5, 6);
        let (x, y) = pair;
        x = 50;                             // a copy: the source keeps 5
        if (pair[0] != 5 or x != 50 or y != 6) { return 4; }

        let (only,) = (42,);
        return only - 42;
    }
    """
    assert run(source).returncode == 0


def test_destructuring_needs_a_matching_tuple(compile_source):
    """
    The value must be a tuple of the pattern's arity, and the pattern
    takes no annotation.
    """
    with pytest.raises(TypeError, match="cannot destructure a 'i32' value"):
        compile_source("""
        fn main() -> i32 { let (a, b) = 5; return 0; }
        """)

    with pytest.raises(TypeError, match="the pattern binds 3 names; "
                                        "'Tuple<i32,i32>' has 2 elements"):
        compile_source("""
        fn main() -> i32 { let (a, b, c) = (1, 2); return 0; }
        """)

    with pytest.raises(SyntaxError, match="a destructuring takes its types "
                                          "from the tuple"):
        compile_source("""
        fn main() -> i32 { let (a, b): Tuple<i32, i32> = (1, 2); return 0; }
        """)


def test_tuple_name_is_reserved(compile_source):
    """
    'Tuple' is builtin: no declaration can take its name.
    """
    with pytest.raises(TypeError, match="'Tuple' is a builtin type"):
        compile_source("""
        struct Tuple<A, B> { a: A; b: B; }
        fn main() -> i32 { return 0; }
        """)
