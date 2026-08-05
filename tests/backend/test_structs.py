"""Feature tests for structs: fields, nesting, passing, and initialization."""

import pytest


def test_field_write_and_read(run):
    """
    Struct fields can be written and read back.
    """
    source = """
    struct Point {
        x: i32;
        y: i32;
    }

    fn main() -> i32 {
        let p: Point;
        p.x = 30;
        p.y = 12;
        return p.x + p.y;
    }
    """
    assert run(source).returncode == 42


def test_forward_declared_struct_defined_later(run):
    """
    A struct may be forward-declared with no body and defined further down.
    """
    source = """
    struct Node;

    struct Node {
        value: i32;
    }

    fn main() -> i32 {
        let n: Node;
        n.value = 11;
        return n.value;
    }
    """
    assert run(source).returncode == 11


def test_opaque_struct_passes_through_pointers(run):
    """
    A struct never given a body is opaque: usable through pointers alone.
    """
    source = """
    struct Handle;

    fn probe(h: Handle*) -> i32 {
        return 21;
    }

    fn main() -> i32 {
        let h: Handle*;
        return probe(h) * 2;
    }
    """
    assert run(source).returncode == 42


def test_structs_resolve_regardless_of_declaration_order(run):
    """
    Sources are parsed first and types resolved later: a field may name a
    struct declared further down, and two structs may point at each other.
    """
    source = """
    struct T {
        s: S*;
        value: i32;
    }

    struct S {
        t: T*;
    }

    fn main() -> i32 {
        let t: T;
        t.value = 9;
        return t.value;
    }
    """
    assert run(source).returncode == 9


def test_nested_structs(run):
    """
    A struct field may itself be a struct, accessed by chained members.
    """
    source = """
    struct Point {
        x: i32;
        y: i32;
    }

    struct Line {
        from: Point;
        to: Point;
    }

    fn main() -> i32 {
        let l: Line;
        l.from.x = 1;
        l.to.x = 10;
        l.to.x += 5;
        return l.to.x - l.from.x; // 14
    }
    """
    assert run(source).returncode == 14


def test_struct_passed_and_returned_by_value(run):
    """
    Structs pass to functions and return from them by value.
    """
    source = """
    struct Point {
        x: i32;
        y: i32;
    }

    fn make(x: i32, y: i32) -> Point {
        let p: Point;
        p.x = x;
        p.y = y;
        return p;
    }

    fn sum(p: Point) -> i32 {
        return p.x + p.y;
    }

    fn main() -> i32 {
        return sum(make(20, 22));
    }
    """
    assert run(source).returncode == 42


def test_aggregate_literal_initialization(run):
    """
    A '{a, b}' literal fills a struct's fields positionally.
    """
    source = """
    struct Pair {
        a: i32;
        b: i32;
    }

    fn main() -> i32 {
        let p: Pair = {17, 25};
        return p.a + p.b;
    }
    """
    assert run(source).returncode == 42


def test_struct_with_trailing_semicolon(run):
    """
    A struct declaration may end with an optional ';'.
    """
    source = """
    struct Wrapped {
        value: i32;
    };

    fn main() -> i32 {
        let w: Wrapped = {9};
        return w.value;
    }
    """
    assert run(source).returncode == 9


def test_private_field_accessible_from_methods(run, compile_source):
    """
    A private field is reachable inside the struct's methods but not
    from free functions.
    """
    source = """
    struct S {
        @private handle: opaque*;
        value: i32;
    }

    fn S::make(handle: opaque*, value: i32) -> S {
        let s: S;
        s.handle = handle;
        s.value = value;
        return s;
    }

    fn S::value(const &self) -> i32 {
        return self.value;
    }

    fn S::handle(const &self) -> const opaque* {
        return self.handle;
    }

    fn main() -> i32 {
        let s = S::make(null, 42);
        if (s.value() != 42) { return 1; }
        if (s.handle() != null) { return 2; }
        return 0;
    }
    """
    assert run(source).returncode == 0

    with pytest.raises(TypeError, match="field 'handle' is private"):
        compile_source(source + """
    fn peek(s: const S) -> const opaque* {
        return s.handle;
    }
""")


def test_private_field_rejects_aggregate_literal_outside_methods(compile_source):
    """Aggregate literals cannot initialize private fields outside methods."""
    source = """
    struct S {
        @private handle: opaque*;
        value: i32;
    }

    fn main() -> i32 {
        let s: S = { handle = null, value = 1 };
        return s.value;
    }
    """
    with pytest.raises(TypeError, match="field 'handle' is private"):
        compile_source(source)
