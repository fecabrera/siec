"""Feature tests for '@macro' declarations."""

import pytest


def test_object_macro_expands_on_its_bare_name(run):
    """
    '@macro name = <expr>;' is object-like, C's 'errno'-style: a bare
    'name' substitutes the expression in place, each use evaluating it.
    """
    source = """
    @static let slot: i32 = 41;

    fn location() -> i32* {
        return &slot;
    }

    @macro stored = *location();

    fn main() -> i32 {
        let seen = stored;         // reads through the call, inferred i32
        if (seen != 41) { return 1; }

        slot = 7;
        if (stored != 7) { return 2; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_object_macro_takes_a_block(run):
    """
    An object-like macro may hold a block, 'emit' producing its value.
    """
    source = """
    @static let calls: i32 = 0;

    @macro next {
        calls += 1;
        emit calls;
    }

    fn main() -> i32 {
        if (next != 1) { return 1; }
        if (next != 2) { return 2; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_function_macro_expression(run):
    """
    A function-like macro with '= <expr>' substitutes the expression,
    arguments in place of parameters.
    """
    source = """
    @macro min(a, b) = a < b ? a : b;

    fn main() -> i32 {
        if (min(3, 5) != 3) { return 1; }

        let x: i64 = 9;
        let y: i64 = 4;
        let m = min(x, y);          // inferred i64
        if (m != 4) { return 2; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_function_macro_block_emits_a_value(run):
    """
    A block-bodied macro produces its value through 'emit'; each 'emit'
    coerces to a typed context's target.
    """
    source = """
    @macro clamp(v, lo, hi) {
        if (v < lo) { emit lo; }
        if (v > hi) { emit hi; }
        emit v;
    }

    fn main() -> i32 {
        let c: i64 = clamp(20, 0, 10);
        if (c != 10) { return 1; }
        if (clamp(5, 0, 10) != 5) { return 2; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_macro_arguments_are_places(run):
    """
    A parameter stands for its argument expression, C-macro-style: an
    assignment to it writes through the caller's variable, member, or
    element.
    """
    source = """
    struct Point { x: i32; y: i32; }

    @macro swap(a, b) {
        let t = a;
        a = b;
        b = t;
    }

    fn main() -> i32 {
        let a = 3;
        let b = 5;
        swap(a, b);
        if (a != 5 or b != 3) { return 1; }

        let p: Point;
        p.x = 1;
        p.y = 2;
        swap(p.x, p.y);
        if (p.x != 2 or p.y != 1) { return 2; }

        let arr: i32[] = [7, 9];
        swap(arr[0], arr[1]);
        if (arr[0] != 9 or arr[1] != 7) { return 3; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_macro_calls_macro(run):
    """
    A macro may use another; a value macro also stands as a statement,
    its value discarded.
    """
    source = """
    @macro twice(v) { emit v + v; }
    @macro quad(v) = twice(v) + twice(v);

    fn main() -> i32 {
        if (quad(2) != 8) { return 1; }
        twice(3);
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_macro_argument_evaluates_per_use(run):
    """
    Substitution is textual: an argument named twice in the body runs
    twice, like a C macro.
    """
    source = """
    @static let counter: i32 = 0;

    fn bump() -> i32 {
        counter += 1;
        return counter;
    }

    @macro twice(v) = v + v;

    fn main() -> i32 {
        let d = twice(bump());   // bump() runs twice: 1 + 2
        if (d != 3) { return 1; }
        return (counter as i32) - 2;
    }
    """
    assert run(source).returncode == 0


def test_macro_without_emit_is_a_statement(run):
    """
    A block macro with no 'emit' produces no value: calling it as a
    statement splices its block, its locals scoped to the expansion.
    """
    source = """
    @macro bump(v) { let step = 1; v = v + step; }

    fn main() -> i32 {
        let a = 41;
        bump(a);
        let step = 100;          // no collision with the macro's local
        return a + step - 142;
    }
    """
    assert run(source).returncode == 0


def test_macro_without_emit_has_no_value(compile_source):
    """
    Using a valueless macro where a value is needed says so.
    """
    with pytest.raises(TypeError, match="macro 'bump' does not 'emit' a value"):
        compile_source("""
        @macro bump(v) { v = v + 1; }
        fn main() -> i32 { let a = 1; let b = bump(a); return b; }
        """)


def test_macro_argument_must_be_assignable_when_written(compile_source):
    """
    A macro assigning to a parameter needs an assignable argument.
    """
    with pytest.raises(TypeError, match="argument must be assignable"):
        compile_source("""
        @macro bump(v) { v = v + 1; }
        fn main() -> i32 { bump(41); return 0; }
        """)


def test_macro_checks_its_arity(compile_source):
    """
    A call must pass exactly one argument per parameter, and an
    object-like macro takes none at all.
    """
    with pytest.raises(TypeError, match="macro 'twice' takes 1 argument"):
        compile_source("""
        @macro twice(v) = v + v;
        fn main() -> i32 { return twice(1, 2); }
        """)

    with pytest.raises(TypeError, match="macro 'seven' takes no parameters"):
        compile_source("""
        @macro seven = 7;
        fn main() -> i32 { return seven(1); }
        """)


def test_macro_cycles_are_rejected(compile_source):
    """
    A macro expanding into itself, straight or roundabout, is an error;
    object-like references count.
    """
    with pytest.raises(TypeError, match="macro cycle: a -> b -> a"):
        compile_source("""
        @macro a(x) { emit b(x); }
        @macro b(x) { emit a(x); }
        fn main() -> i32 { return a(1); }
        """)

    with pytest.raises(TypeError, match="macro cycle: loop -> loop"):
        compile_source("""
        @macro loop = loop + 1;
        fn main() -> i32 { return loop; }
        """)


def test_macro_name_collides_with_constants(compile_source):
    """
    Macros share the '@const' namespace: one name, one declaration.
    """
    with pytest.raises(TypeError, match="declared more than once"):
        compile_source("""
        @const size = 4;
        @macro size(v) = v;
        fn main() -> i32 { return 0; }
        """)


def test_object_macro_assigns_through_its_expansion(run):
    """
    A macro whose expansion is an assignable place takes assignments,
    C's 'errno = EINVAL'-style: the store goes through the expansion,
    compound operators included.
    """
    source = """
    @static let slot: i32 = 0;

    fn location() -> i32* {
        return &slot;
    }

    @macro stored = *location();

    fn main() -> i32 {
        stored = 40;
        stored += 2;
        if (slot != 42) { return 1; }
        return stored - 42;
    }
    """
    assert run(source).returncode == 0


def test_function_macro_assigns_through_its_expansion(run):
    """
    A function-like macro's call assigns the place its expansion names;
    members and elements of a macro place write through too.
    """
    source = """
    struct Point { x: i32; y: i32; }

    @static let points: Point* = null;

    @macro at(i) = points[i];
    @macro first = points[0];

    fn main() -> i32 {
        let backing: Point[2];
        points = backing.data;

        at(0) = { x = 1, y = 2 };
        at(1).y = 5;
        first.x = 7;
        if (backing[0].x != 7 or backing[0].y != 2) { return 1; }
        if (backing[1].y != 5) { return 2; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_macro_assignment_needs_a_place(compile_source):
    """
    Assigning through a macro whose expansion is no lvalue is an error.
    """
    with pytest.raises(TypeError, match="macro 'seven' does not expand to "
                                        "an assignable place"):
        compile_source("""
        @macro seven = 7;
        fn main() -> i32 { seven = 1; return 0; }
        """)
