"""Feature tests for struct field defaults: 'name: type = value;'."""

import pytest


def test_fields_infer_enum_bool_and_integer_defaults(run):
    """A field without an annotation adopts its default value's type."""
    source = """
    struct State {
        mode = Mode::Ready;
        open = false;
        count = 40;
    }

    enum Mode {
        None,
        Ready,
    }

    fn main() -> i32 {
        let state: State;
        if (state.mode != Mode::Ready or state.open) { return 1; }
        state.open = true;
        return state.count + (state.open ? 2 : 0);
    }
    """
    assert run(source).returncode == 42


def test_generic_field_infers_a_type_parameter_from_a_cast(run):
    """A generic field may infer a type that contains its placeholder."""
    source = """
    struct Box<T> {
        value = 40 as T;
        ready = true;
    }

    fn main() -> i32 {
        let box: Box<i32>;
        return box.value + (box.ready ? 2 : 0);
    }
    """
    assert run(source).returncode == 42


def test_inferred_fields_are_sized_before_enum_values(run):
    """An enum may measure a struct whose field types are inferred."""
    source = """
    struct S {
        ready = false;
        count = 1;
    }

    enum Size {
        S = @sizeof(S),
    }

    fn main() -> i32 {
        return Size::S as i32;
    }
    """
    assert run(source).returncode == 8


def test_inferred_field_needs_a_default_with_a_known_type(compile_source):
    """An empty literal cannot determine an unannotated field's type."""
    with pytest.raises(TypeError, match=(
            "cannot infer a type for field 'items': annotate it explicitly")):
        compile_source("""
        struct S { items = []; }
        fn main() -> i32 { return 0; }
        """)


def test_bare_declarations_start_from_the_defaults(run):
    """
    'let s: S;' on a struct with defaults holds them, undefaulted fields
    zeroed; nested struct defaults cascade.
    """
    source = """
    struct List<T> {
        data: T* = null;
        length: u64;
        capacity: u64 = 8;
    }

    struct Config {
        verbose: bool = true;
        level: i32 = 40;
    }

    struct App {
        cfg: Config;
        id: i32;
    }

    fn main() -> i32 {
        let l: List<i32>;
        let a: App;

        if (l.data != null or l.length != 0 or l.capacity != 8) { return 1; }
        if (not a.cfg.verbose or a.id != 0) { return 2; }

        return a.cfg.level + 2;
    }
    """
    assert run(source).returncode == 42


def test_named_literals_default_the_unfilled_fields(run):
    """
    A named literal fills what it names; the rest take their defaults
    instead of zero.
    """
    source = """
    struct Config {
        verbose: bool = true;
        level: i32 = 40;
        tag: char* = "sie";
    }

    fn main() -> i32 {
        let c: Config = { level = 2 };

        if (not c.verbose or c.tag[0] != "s"[0]) { return 1; }
        return c.level + 40;
    }
    """
    assert run(source).returncode == 42


def test_union_fields_take_no_default(compile_source):
    """
    A union's fields share one storage; no single member's default could
    fill it.
    """
    with pytest.raises(TypeError, match="a union field cannot have a default"):
        compile_source("""
        union U { i: i64 = 5; f: f64; }
        fn main() -> i32 { return 0; }
        """)
