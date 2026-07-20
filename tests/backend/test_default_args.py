"""Feature tests for default arguments: 'fn f(a: i32, b: i32 = N)'."""

import pytest


def test_defaults_fill_omitted_arguments(run):
    """
    A call may omit trailing defaulted arguments: each takes its declared
    default, which evaluates like an expression written at the call.
    """
    source = """
    @const BASE = 30;

    fn add(a: i32, b: i32 = 2, c: i32 = BASE + 10) -> i32 {
        return a + b + c;
    }

    fn main() -> i32 {
        // 43 + 44 + 8
        return add(1) + add(1, 3) + add(1, 3, 4) - 95;
    }
    """
    assert run(source).returncode == 0


def test_defaults_on_methods_and_constructors(run):
    """
    Methods fill their defaults from either call form, and 'S(...)'
    fills init's; a generic struct's stamp carries them along.
    """
    source = """
    struct Counter { count: i32; }
    fn Counter::init(self: &Counter, start: i32 = 5) { self.count = start; }
    fn Counter::bump(self: &Counter, by: i32 = 3) { self.count += by; }

    struct List<T> { length: u64; capacity: u64; }
    fn List<T>::init(self: &List<T>, capacity: u64 = 8) {
        self.length = 0;
        self.capacity = capacity;
    }

    fn pick<T>(a: T, b: T = 100) -> T { return a > b ? a : b; }

    fn main() -> i32 {
        let c = Counter();              // constructor default
        c.bump();                       // method default
        Counter::bump(c);               // qualified form too
        c.bump(2);

        let l = List<i32>();            // generic constructor default

        // 5+3+3+2 + 8 + 100 + 7 - 128
        return c.count + l.capacity as i32 + pick(7) + pick(7, 3) - 128;
    }
    """
    assert run(source).returncode == 0


def test_a_default_needs_the_declaring_files_view_only(tmp_path, monkeypatch):
    """
    A default referencing the declaring file's own names — a '@const', a
    '@static' global — still fills a call in a file that sees neither.
    """
    from tests.cli.test_cli import run_cli

    mod = tmp_path / "coll"
    mod.mkdir()
    (mod / "list.sie").write_text("""
        @static let extra: u64 = 7;
        @const CAP = 9;

        struct List<T> { length: u64; capacity: u64; }
        fn List<T>::init(self: &List<T>, capacity: u64 = CAP + extra) {
            self.length = 0;
            self.capacity = capacity;
        }
    """)

    src = tmp_path / "main.sie"
    src.write_text("""
        import { List } from coll.list;

        fn main() -> i32 {
            let l = List<i32>();
            return (l.capacity as i32) - 16;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 0


def test_a_gap_before_a_default_is_an_error(compile_source):
    """
    Defaults fill omitted trailing arguments, so only the last parameters
    can carry them.
    """
    with pytest.raises(SyntaxError, match="parameter 'b' needs a default: "
                                          "it follows a defaulted parameter"):
        compile_source("""
        fn f(a: i32 = 1, b: i32) -> i32 { return a + b; }
        fn main() -> i32 { return f(1, 2); }
        """)


def test_undefaulted_arguments_stay_required(compile_source):
    """
    Omitting an argument with no default is still an arity error.
    """
    with pytest.raises(TypeError, match="too few arguments"):
        compile_source("""
        fn f(a: i32, b: i32 = 1) -> i32 { return a + b; }
        fn main() -> i32 { return f(); }
        """)
