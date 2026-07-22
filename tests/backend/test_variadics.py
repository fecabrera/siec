"""Feature tests for 'args...' variadic functions."""

import pytest


def test_variadics_pack_into_any_arrays(run):
    """
    'fn f(s: T, args...)' is 'args: Any[]': extra call arguments wrap
    and pack, none packs empty, an Any[] argument forwards as-is, and
    methods take the sugar too.
    """
    source = """
    struct Counter { count: i32; }
    fn Counter::init(&self) { self.count = 0; }
    fn Counter::tally(&self, args...) {
        self.count += args.length as i32;
    }

    fn describe(str: const char[], args...) -> u64 {
        let ints: u64 = 0;
        foreach (arg : args) {
            if (@typeof(arg) == i32) {
                ints += 1;
            }
        }
        return str.length + args.length * 10 + ints * 100;
    }

    fn relay(str: const char[], args...) -> u64 {
        return describe(str, args);        // forwards, not re-packs
    }

    fn main() -> i32 {
        if (describe("hello world") != 11) { return 1; }        // 0 args
        if (describe("hello {}", "world") != 18) { return 2; }  // 1 arg
        if (describe("x", 1, 2, 2.5) != 231) { return 3; }      // mixed
        if (relay("ab", 1, "c", 3) != 232) { return 4; }

        let c = Counter();
        c.tally();
        c.tally(1, "two", 3.0);
        return c.count - 3;
    }
    """
    assert run(source).returncode == 0


def test_variadics_overload(run):
    """
    Variadic overloads pick by their fixed parameters; extras never
    confuse the pick, and forwarding flows through the picked one.
    """
    source = """
    fn log(tag: i32, args...) -> u64 { return 100 + args.length; }
    fn log(str: const char[], args...) -> u64 { return 200 + args.length; }
    fn relay(str: const char[], args...) -> u64 { return log(str, args); }

    fn main() -> i32 {
        if (log(1) != 100) { return 1; }
        if (log(1, "a", 2) != 102) { return 2; }
        if (log("x") != 200) { return 3; }
        if (log("x", 5) != 201) { return 4; }
        if (relay("y", 1, 2, 3) != 203) { return 5; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_variadic_must_close_the_parameters(compile_source):
    """
    'args...' only fits last, and '@extern' keeps C varargs.
    """
    with pytest.raises(SyntaxError, match="expected '\\)', got ','"):
        compile_source("""
        fn f(args..., x: i32) { }
        fn main() -> i32 { return 0; }
        """)

    with pytest.raises(SyntaxError, match="an '@extern' function takes C "
                                          "varargs: a bare '...'"):
        compile_source("""
        @extern fn f(args...);
        fn main() -> i32 { return 0; }
        """)
