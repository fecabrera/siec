"""Feature tests for the '@typename' compile-time macro."""

import pytest


def test_typename_bakes_canonical_names(run):
    """
    '@typename(v)' is the canonical name of v's type as a 'const
    char[]': aliases expand, arrays and generics spell out, and a type
    written directly resolves the same way.
    """
    source = """
    struct List<T> { data: T*; length: u64; }
    @type String = List<char>;

    fn same(a: const char[], b: const char[]) -> bool {
        if (a.length != b.length) { return false; }
        for (let i: u64 = 0; i < a.length; i += 1) {
            if (a[i] != b[i]) { return false; }
        }
        return true;
    }

    fn name_of<T>(v: T) -> const char[] {
        return @typename(T);
    }

    fn main() -> i32 {
        let num: u64;
        if (not same(@typename(num), "u64")) { return 1; }

        let s: String;
        if (not same(@typename(s), "List<char>")) { return 2; }

        let arr: i32[];
        if (not same(@typename(arr), "i32[]")) { return 3; }

        let lst: List<f64>;
        if (not same(@typename(lst), "List<f64>")) { return 4; }

        if (not same(@typename(String), "List<char>")) { return 5; }
        if (not same(@typename(i32*), "i32*")) { return 6; }
        if (not same(@typename(Tuple<i32, f64>), "Tuple<i32,f64>")) { return 7; }

        // inside a generic, T substitutes before the name bakes in
        if (not same(name_of(1.5), "f64")) { return 8; }
        if (not same(name_of(lst), "List<f64>")) { return 9; }

        let n = @typename(num);              // an ordinary const char[]
        return (n.length as i32) - 3;
    }
    """
    assert run(source).returncode == 0


def test_typename_rejects_unknown_names(compile_source):
    """
    A name that is neither in scope nor a type is an error, not a string.
    """
    with pytest.raises(TypeError, match="unknown type 'wat'"):
        compile_source("""
        fn main() -> i32 { let n = @typename(wat); return 0; }
        """)
