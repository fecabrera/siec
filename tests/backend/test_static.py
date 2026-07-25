"""Feature tests for '@static' file-local functions."""

import pytest


def test_static_gets_internal_linkage(compile_source):
    """
    '@static' lowers to an internal-linkage function under a mangled symbol.
    """
    module = compile_source("""
    @static fn helper() -> i32 { return 1; }
    fn main() -> i32 { return helper(); }
    """)
    assert "define internal" in str(module)


def test_static_is_callable_in_its_own_file(run):
    """
    Within its file, a static calls and references like any function.
    """
    source = """
    @static fn double(n: i32) -> i32 {
        return n * 2;
    }

    fn main() -> i32 {
        let f = double;             // a reference resolves too
        return f(10) + double(11);  // 20 + 22
    }
    """
    assert run(source).returncode == 42


def test_static_main_is_an_error(compile_source):
    """
    'main' must stay visible to the C runtime.
    """
    with pytest.raises(TypeError, match="'main' cannot be static"):
        compile_source("@static fn main() -> i32 { return 0; }")


def test_a_static_template_instantiates_under_its_own_symbol(run):
    """
    A '@static' function with an interface parameter is a template; its
    instances mangle at instantiation, so the call finds the symbol the
    declaration landed under.
    """
    source = """
    @static
    fn helper(v: const &Iterable<char>) -> u64 {
        let n: u64 = 0;
        foreach (c : v) {
            n += 1;
        }
        return n;
    }

    fn main() -> i32 {
        return (helper("hello") as i32) - 5;
    }
    """
    assert run(source).returncode == 0
