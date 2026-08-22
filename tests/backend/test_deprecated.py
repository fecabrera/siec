"""Feature tests for '@deprecated' warnings and '@remove' errors."""

import pytest

from siec.codegen.errors import format_diagnostic


def warnings_of(compile_source, source: str) -> str:
    """
    Compile source and render the structured warnings the compiler recorded.
    """
    module = compile_source(source)
    rendered = [
        format_diagnostic(diagnostic)
        for diagnostic in getattr(module, "sie_diagnostics", ())
        if diagnostic.severity == "warning"
    ]
    return "\n".join(rendered) + ("\n" if rendered else "")


def test_reachable_use_warns(compile_source):
    """
    A call to a '@deprecated' function from code 'main' reaches warns
    with the declared advice, at the call site's line.
    """
    warned = warnings_of(compile_source, """
    fn new_func() -> i32 { return 1; }

    @deprecated("use new_func")
    fn old_func() -> i32 { return 0; }

    fn main() -> i32 {
        return old_func() + new_func() - 1;
    }
    """)

    assert "line 8: warning: 'old_func' is deprecated: use new_func" in warned
    assert warned.count("warning") == 1


def test_bare_deprecated_warns_without_advice(compile_source):
    """A bare decorator warns without appending an empty advice suffix."""
    warned = warnings_of(compile_source, """
    @deprecated fn old() -> i32 { return 1; }

    fn main() -> i32 { return old() - 1; }
    """)

    assert "warning: 'old' is deprecated" in warned
    assert "deprecated:" not in warned


def test_transitive_uses_warn(compile_source):
    """
    Reachability follows the call graph: a use inside a function 'main'
    reaches through other calls warns too.
    """
    warned = warnings_of(compile_source, """
    @deprecated("use later") fn old() -> i32 { return 1; }

    fn middle() -> i32 { return old(); }
    fn outer() -> i32 { return middle(); }

    fn main() -> i32 { return outer() - 1; }
    """)

    assert "'old' is deprecated: use later" in warned


def test_unreachable_use_stays_quiet(compile_source):
    """
    A use no path from 'main' arrives at is not reported: the program
    never runs it.
    """
    warned = warnings_of(compile_source, """
    @deprecated("use later") fn old() -> i32 { return 1; }

    fn never_called() -> i32 { return old(); }

    fn main() -> i32 { return 0; }
    """)

    assert warned == ""


def test_deprecated_callers_stay_quiet(compile_source):
    """
    A deprecated function may lean on its own generation: uses inside one
    are not reported.
    """
    warned = warnings_of(compile_source, """
    @deprecated("use later") fn old() -> i32 { return 1; }

    @deprecated("use later too")
    fn old_helper() -> i32 { return old(); }

    fn main() -> i32 { return old_helper() - 1; }
    """)

    assert "'old_helper' is deprecated: use later too" in warned
    assert "'old' is deprecated" not in warned


def test_references_warn(compile_source):
    """
    Handing a deprecated function around reaches it as surely as calling
    it: the reference warns, and makes it reachable from there.
    """
    warned = warnings_of(compile_source, """
    @deprecated("use later") fn old() -> i32 { return 1; }

    fn main() -> i32 {
        let f = old;
        return f() - 1;
    }
    """)

    assert "line 5: warning: 'old' is deprecated: use later" in warned


def test_methods_and_generics_warn(compile_source):
    """
    A method or a generic function deprecates like any other; a generic's
    warning names the instance the call stamped.
    """
    warned = warnings_of(compile_source, """
    struct Box { v: i32; }

    @deprecated("use Box::value") fn Box::get(&self) -> i32 { return self.v; }
    fn Box::value(&self) -> i32 { return self.v; }

    @deprecated("use scale2") fn scale<T>(v: T) -> T { return v; }

    fn main() -> i32 {
        let b: Box;
        b.v = 21;
        return b.get() + scale(21) - 42;
    }
    """)

    assert "'Box::get' is deprecated: use Box::value" in warned
    assert "'scale<i32>' is deprecated: use scale2" in warned


def test_one_warning_per_site(compile_source):
    """
    A site warns once, however many times emission passes it; two sites
    warn twice.
    """
    warned = warnings_of(compile_source, """
    @deprecated("use later") fn old() -> i32 { return 1; }

    fn twice<T>(v: T) -> i32 { return old(); }

    fn main() -> i32 {
        let a = twice(1);
        let b = twice(1.5);         // a second instance, the same site
        return old() + a + b - 3;
    }
    """)

    assert warned.count("warning") == 2


def test_a_unit_without_main_reports_every_use(compile_source):
    """
    A library unit has no 'main' to walk from, so anything it defines may
    be an entry: its uses all report.
    """
    warned = warnings_of(compile_source, """
    @deprecated("use later") fn old() -> i32 { return 1; }

    fn exported() -> i32 { return old(); }
    """)

    assert "'old' is deprecated: use later" in warned


def test_deprecation_does_not_fail_the_build(run):
    """
    A warning describes code that compiles: the program still builds and
    runs.
    """
    source = """
    @deprecated("use later") fn old() -> i32 { return 42; }

    fn main() -> i32 { return old() - 42; }
    """
    assert run(source).returncode == 0


def test_removed_use_is_an_error(compile_source):
    """
    A '@remove' function's declaration stands so its uses name it, and
    each use fails with the advice it declared.
    """
    with pytest.raises(TypeError, match="'old_func' was removed: use new_func"):
        compile_source("""
        fn new_func() -> i32 { return 1; }

        @remove("use new_func") fn old_func() -> i32;

        fn main() -> i32 { return old_func(); }
        """)


def test_removed_functions_may_go_unused(run):
    """
    The tombstone itself compiles: only a use of it fails.
    """
    source = """
    @remove("use new_func") fn old_func() -> i32;

    fn main() -> i32 { return 0; }
    """
    assert run(source).returncode == 0


def test_removed_uses_fail_wherever_they_sit(compile_source):
    """
    Unlike a deprecation, a removal is not gated by reachability: the
    code cannot compile at all.
    """
    with pytest.raises(TypeError, match="'old' was removed: gone"):
        compile_source("""
        @remove("gone") fn old() -> i32;

        fn never_called() -> i32 { return old(); }

        fn main() -> i32 { return 0; }
        """)


def test_removed_references_fail(compile_source):
    """
    Handing a removed function around fails like calling it.
    """
    with pytest.raises(TypeError, match="'old' was removed: gone"):
        compile_source("""
        @remove("gone") fn old() -> i32;

        fn main() -> i32 { let f = old; return f(); }
        """)


def test_removed_methods_and_generics_fail(compile_source):
    """
    Methods, generic functions, a generic struct's methods, and an
    array's all deprecate to nothing the same way; a removed template
    never registers, so its name answers for the advice.
    """
    with pytest.raises(TypeError, match="'Box::get' was removed: use Box::value"):
        compile_source("""
        struct Box { v: i32; }
        @remove("use Box::value") fn Box::get(&self) -> i32;
        fn Box::value(&self) -> i32 { return self.v; }

        fn main() -> i32 { let b: Box; b.v = 1; return b.get(); }
        """)

    with pytest.raises(TypeError, match="'scale' was removed: use scale2"):
        compile_source("""
        @remove("use scale2") fn scale<T>(v: T) -> T;
        fn main() -> i32 { return scale(1); }
        """)

    with pytest.raises(TypeError, match="'Box::get' was removed"):
        compile_source("""
        struct Box<T> { v: T; }
        @remove("use Box::value") fn Box<T>::get(&self) -> T;
        fn Box<T>::value(&self) -> T { return self.v; }

        fn main() -> i32 { let b: Box<i32>; b.v = 1; return b.get(); }
        """)

    with pytest.raises(TypeError, match=r"'T\[\]::walk' was removed"):
        compile_source("""
        @remove("use foreach") fn T[]::walk(&self) -> u64;
        fn main() -> i32 { let a: i32[] = [1]; return a.walk() as i32; }
        """)


def test_removed_functions_take_no_body(compile_source):
    """
    There is nothing left to define: a body is a syntax error.
    """
    with pytest.raises(SyntaxError, match="'@remove' function cannot have a body"):
        compile_source("""
        @remove("gone") fn old() -> i32 { return 1; }
        fn main() -> i32 { return 0; }
        """)
