"""Feature tests for '@static let' file-local globals."""

import pytest


def test_static_global_holds_state_across_calls(run):
    """
    A static global is one storage location, shared by every call.
    """
    source = """
    @static let count: i32 = 39;

    fn bump() -> i32 {
        count += 1;
        return count;
    }

    fn main() -> i32 {
        bump();
        bump();
        return bump(); // 42
    }
    """
    assert run(source).returncode == 42


def test_static_global_defaults_to_zero(run):
    """
    Without an initializer, a static global starts at zero, C-style.
    """
    source = """
    @static let count: i64;

    fn main() -> i32 {
        return count as i32 + 42;
    }
    """
    assert run(source).returncode == 42


def test_static_global_initializers(compile_source):
    """
    Integers, floats, bools, strings, and enum members all initialize.
    """
    module = str(compile_source("""
    enum Color { RED, GREEN }

    @const BASE = 40;

    @static let count: i32 = BASE + 2;
    @static let scale: f64 = 1.5;
    @static let ready: bool = true;
    @static let name: char* = "sie";
    @static let mode: i32 = Color::GREEN;
    """))
    assert "internal global i32 42" in module
    assert "internal global double" in module
    assert "internal global i1 1" in module
    assert 'bitcast' in module and '".str.0"' in module
    assert "internal global i32 2" in module


def test_static_sized_array(run):
    """
    A static 'X[N]' gets N zeroed elements of module storage, writable
    from any function and persistent across calls.
    """
    source = """
    @static let buf: i32[8];

    fn record(slot: u64, v: i32) {
        buf[slot] = v;
    }

    fn main() -> i32 {
        record(0, 30);
        record(1, 2);
        return buf[0] + buf[1] + buf.length as i32 + buf[7]; // 30+2+8+0
    }
    """
    assert run(source).returncode == 40


def test_static_sized_array_backing_is_module_storage(compile_source):
    """
    The array's value points at a module-level backing of N elements.
    """
    module = str(compile_source("@static let buf: i32[8];"))
    assert "internal global [8 x i32] zeroinitializer" in module
    assert "getelementptr" in module and "i64 8" in module


def test_static_sized_array_rejects_an_initializer(compile_source):
    """
    Like a local, a static sized array takes its contents from its size.
    """
    with pytest.raises(TypeError, match="takes its contents from its size"):
        compile_source("@static let buf: i32[8] = [1, 2];")


def test_static_struct_aggregate_initializer(run):
    """
    A positional aggregate builds a static struct's value at compile time.
    """
    source = """
    struct Point {
        x: i32;
        y: i32;
    }

    @static let start: Point = {40, 2};

    fn main() -> i32 {
        return start.x + start.y;
    }
    """
    assert run(source).returncode == 42


def test_static_struct_named_initializer_zero_fills(run):
    """
    A named initializer fills its fields; the rest start at zero.
    """
    source = """
    struct Point {
        x: i32;
        y: i32;
    }

    @static let p: Point = { y = 42 };

    fn main() -> i32 {
        return p.y - p.x;
    }
    """
    assert run(source).returncode == 42


def test_static_struct_initializer_mixes_constant_kinds(run):
    """
    Fields take strings, enum members, '@const' refs, and nested structs.
    """
    source = """
    enum Mode { OFF = 0, ON }

    @const BASE = 30;

    struct Inner {
        n: i32;
    }

    struct Config {
        name: char*;
        mode: Mode;
        limit: i32;
        inner: Inner;
    }

    @static let config: Config = {
        name = "cfg",
        mode = Mode::ON,
        limit = BASE + 2,
        inner = { 9 }
    };

    fn main() -> i32 {
        if (config.name[0] != "c"[0]) {
            return 0;
        }
        return config.limit + config.inner.n + config.mode as i32; // 32+9+1
    }
    """
    assert run(source).returncode == 42


def test_static_struct_initializer_checks_the_element_count(compile_source):
    """
    A positional initializer must fill every field.
    """
    with pytest.raises(TypeError, match="has 1 elements, expected 2"):
        compile_source("""
        struct Point { x: i32; y: i32; }
        @static let p: Point = {1};
        """)


def test_static_struct_initializer_rejects_unknown_fields(compile_source):
    """
    A named initializer only names declared fields.
    """
    with pytest.raises(TypeError, match="unknown field 'z'"):
        compile_source("""
        struct Point { x: i32; y: i32; }
        @static let p: Point = { z = 1 };
        """)


def test_static_struct_initializer_fields_must_be_constant(compile_source):
    """
    Every field's value must be known at compile time.
    """
    with pytest.raises(TypeError, match="constant integer expression"):
        compile_source("""
        struct Point { x: i32; y: i32; }
        fn f() -> i32 { return 1; }
        @static let p: Point = { x = f() };
        """)


def test_static_struct_global(run):
    """
    A static struct is zero-initialized module storage, its fields read
    and written in place from any function.
    """
    source = """
    struct Point {
        x: i32;
        y: i32;
    }

    @static let origin: Point;

    fn shift(dx: i32) {
        origin.x += dx;
    }

    fn main() -> i32 {
        shift(40);
        shift(2);
        origin.y = origin.x - 42;
        return origin.x + origin.y; // 42 + 0
    }
    """
    assert run(source).returncode == 42


def test_const_static_global_cannot_be_assigned(compile_source):
    """
    A 'const' static global rejects assignment like any const variable.
    """
    with pytest.raises(TypeError, match="cannot assign to const variable"):
        compile_source("""
        @static let limit: const i32 = 5;
        fn main() -> i32 { limit = 6; return 0; }
        """)


def test_non_constant_initializer_is_an_error(compile_source):
    """
    A static global's initial value must be known at compile time.
    """
    with pytest.raises(TypeError, match="constant integer expression"):
        compile_source("""
        fn f() -> i32 { return 1; }
        @static let x: i32 = f();
        """)


def test_string_initializer_needs_a_char_pointer(compile_source):
    """
    A string initializer only fits a 'char*' global.
    """
    with pytest.raises(TypeError, match="cannot initialize"):
        compile_source('@static let x: i32 = "hi";')
