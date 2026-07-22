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


def test_typename_takes_expressions(run):
    """
    '@typename'/'@typeid' of an expression name its static type: an
    indexed element, an arithmetic result, an Any folding to 'Any'.
    """
    source = """
    fn same(a: const char[], b: const char[]) -> bool {
        if (a.length != b.length) { return false; }
        for (let i: u64 = 0; i < a.length; i += 1) {
            if (a[i] != b[i]) { return false; }
        }
        return true;
    }

    fn main() -> i32 {
        let nums: f64[] = [1.5, 2.5];
        if (not same(@typename(nums[0]), "f64")) { return 1; }

        let n: i32 = 4;
        if (not same(@typename(n + 1), "i32")) { return 2; }
        if (@typeid(n + 1) != @typeid(i32)) { return 3; }

        let args: Any[] = [n as Any];
        if (not same(@typename(args[0]), "Any")) { return 4; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_cast_to_foreign_alias_target(tmp_path, monkeypatch):
    """
    'x as String' works where String is member-imported but its target's
    generic struct is not: the canonical result never re-gates.
    """
    from tests.cli.test_cli import run_cli

    coll = tmp_path / "coll"
    coll.mkdir()
    (coll / "list.sie").write_text("""
        struct List<T> { length: u64; }
        @type Text = List<char>;
    """)

    src = tmp_path / "main.sie"
    src.write_text("""
        import { Text } from coll.list;

        fn main() -> i32 {
            let t: Text;
            t.length = 0;
            let a = (t as Any) as Text;
            return a.length as i32;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 0


def test_typename_rejects_unknown_names(compile_source):
    """
    A name that is neither in scope nor a type is an error, not a string.
    """
    with pytest.raises(TypeError, match="unknown type 'wat'"):
        compile_source("""
        fn main() -> i32 { let n = @typename(wat); return 0; }
        """)
