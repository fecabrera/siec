"""Feature tests for '@deprecated': warnings at reachable uses."""


def warnings_of(compile_source, capsys, source: str) -> str:
    """
    Compile source and hand back what the compiler warned on stderr.
    """
    compile_source(source)
    return capsys.readouterr().err


def test_reachable_use_warns(compile_source, capsys):
    """
    A call to a '@deprecated' function from code 'main' reaches warns
    with the declared advice, at the call site's line.
    """
    warned = warnings_of(compile_source, capsys, """
    fn new_func() -> i32 { return 1; }

    @deprecated("use new_func")
    fn old_func() -> i32 { return 0; }

    fn main() -> i32 {
        return old_func() + new_func() - 1;
    }
    """)

    assert "line 8: warning: 'old_func' is deprecated: use new_func" in warned
    assert warned.count("warning") == 1


def test_transitive_uses_warn(compile_source, capsys):
    """
    Reachability follows the call graph: a use inside a function 'main'
    reaches through other calls warns too.
    """
    warned = warnings_of(compile_source, capsys, """
    @deprecated("use later") fn old() -> i32 { return 1; }

    fn middle() -> i32 { return old(); }
    fn outer() -> i32 { return middle(); }

    fn main() -> i32 { return outer() - 1; }
    """)

    assert "'old' is deprecated: use later" in warned


def test_unreachable_use_stays_quiet(compile_source, capsys):
    """
    A use no path from 'main' arrives at is not reported: the program
    never runs it.
    """
    warned = warnings_of(compile_source, capsys, """
    @deprecated("use later") fn old() -> i32 { return 1; }

    fn never_called() -> i32 { return old(); }

    fn main() -> i32 { return 0; }
    """)

    assert warned == ""


def test_deprecated_callers_stay_quiet(compile_source, capsys):
    """
    A deprecated function may lean on its own generation: uses inside one
    are not reported.
    """
    warned = warnings_of(compile_source, capsys, """
    @deprecated("use later") fn old() -> i32 { return 1; }

    @deprecated("use later too")
    fn old_helper() -> i32 { return old(); }

    fn main() -> i32 { return old_helper() - 1; }
    """)

    assert "'old_helper' is deprecated: use later too" in warned
    assert "'old' is deprecated" not in warned


def test_references_warn(compile_source, capsys):
    """
    Handing a deprecated function around reaches it as surely as calling
    it: the reference warns, and makes it reachable from there.
    """
    warned = warnings_of(compile_source, capsys, """
    @deprecated("use later") fn old() -> i32 { return 1; }

    fn main() -> i32 {
        let f = old;
        return f() - 1;
    }
    """)

    assert "line 5: warning: 'old' is deprecated: use later" in warned


def test_methods_and_generics_warn(compile_source, capsys):
    """
    A method or a generic function deprecates like any other; a generic's
    warning names the instance the call stamped.
    """
    warned = warnings_of(compile_source, capsys, """
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


def test_one_warning_per_site(compile_source, capsys):
    """
    A site warns once, however many times emission passes it; two sites
    warn twice.
    """
    warned = warnings_of(compile_source, capsys, """
    @deprecated("use later") fn old() -> i32 { return 1; }

    fn twice<T>(v: T) -> i32 { return old(); }

    fn main() -> i32 {
        let a = twice(1);
        let b = twice(1.5);         // a second instance, the same site
        return old() + a + b - 3;
    }
    """)

    assert warned.count("warning") == 2


def test_a_unit_without_main_reports_every_use(compile_source, capsys):
    """
    A library unit has no 'main' to walk from, so anything it defines may
    be an entry: its uses all report.
    """
    warned = warnings_of(compile_source, capsys, """
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
