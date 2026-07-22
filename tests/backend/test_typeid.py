"""Feature tests for the '@typeid' compile-time macro."""

import pytest


def test_typeid_hashes_canonical_names(run):
    """
    '@typeid(v)' is the 64-bit FNV-1a hash of the canonical type name:
    aliases share their target's identity, distinct types differ, and
    being a constant it feeds '@const' values and case arms.
    """
    source = """
    struct List<T> { data: T*; length: u64; }
    @type String = List<char>;

    @const U64_ID = @typeid(u64);

    fn id_of<T>(v: T) -> u64 {
        return @typeid(T);
    }

    fn kind(id: u64) -> i32 {
        case (id) {
            when @typeid(u64): return 1;
            when @typeid(List<char>): return 2;
            else: return 0;
        }
    }

    fn main() -> i32 {
        let num: u64;
        if (@typeid(num) != 5563585020063213298) { return 1; }
        if (@typeid(num) != U64_ID) { return 2; }

        let s: String;
        if (@typeid(s) != @typeid(List<char>)) { return 3; }
        if (@typeid(s) == @typeid(List<u8>)) { return 4; }

        let arr: i32[];
        if (@typeid(arr) != 5695918721817201349) { return 5; }

        if (id_of(num) != U64_ID) { return 6; }
        if (id_of(s) != @typeid(String)) { return 7; }

        if (kind(@typeid(num)) != 1) { return 8; }
        if (kind(@typeid(s)) != 2) { return 9; }
        if (kind(@typeid(f32)) != 0) { return 10; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_typeid_rejects_unknown_names(compile_source):
    """
    A name that is neither in scope nor a type is an error.
    """
    with pytest.raises(TypeError, match="unknown type 'wat'"):
        compile_source("""
        fn main() -> i32 { let n = @typeid(wat); return 0; }
        """)
