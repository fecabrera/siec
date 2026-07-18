"""Feature tests for named aggregate literals '{x = a, y = b}'."""

import pytest

POINT = """
struct Point {
    x: i32;
    y: i32;
}
"""


def test_named_fields_fill_in_any_order(run):
    """
    Fields go by name, not position.
    """
    source = POINT + """
    fn main() -> i32 {
        let p: Point = { y = 2, x = 40 };
        return p.x + p.y;
    }
    """
    assert run(source).returncode == 42


def test_unnamed_fields_zero_initialize(run):
    """
    A named literal may fill a subset; the rest start at zero.
    """
    source = POINT + """
    fn main() -> i32 {
        let p: Point = { x = 42 };
        return p.x + p.y;
    }
    """
    assert run(source).returncode == 42


def test_named_literal_as_an_argument(run):
    """
    The named form works anywhere the positional one does.
    """
    source = POINT + """
    fn norm1(p: Point) -> i32 {
        return p.x + p.y;
    }

    fn main() -> i32 {
        return norm1({ x = 40, y = 2 });
    }
    """
    assert run(source).returncode == 42


def test_named_array_literal(run):
    """
    An array's synthetic 'data' and 'length' fields name too.
    """
    source = """
    fn main() -> i32 {
        let raw: i32[] = [40, 2, 9];
        let view: i32[] = { data = raw.data, length = 2 };
        return view[0] + view.length as i32;
    }
    """
    assert run(source).returncode == 42


def test_static_struct_initializers(run):
    """
    Both literal forms initialize a static struct at compile time.
    """
    source = POINT + """
    @static let a: Point = { 40, 2 };
    @static let b: Point = { y = 2 };

    fn main() -> i32 {
        return a.x + a.y + b.y - b.x - 2; // 40+2+2-0-2
    }
    """
    assert run(source).returncode == 42


def test_unknown_field_is_an_error(compile_source):
    """
    Naming a field the struct does not declare is rejected.
    """
    with pytest.raises(TypeError, match="unknown field 'z'"):
        compile_source(POINT + """
        fn main() -> i32 { let p: Point = { z = 1 }; return 0; }
        """)


def test_duplicate_field_is_an_error(compile_source):
    """
    Setting the same field twice is rejected.
    """
    with pytest.raises(TypeError, match="field 'x' more than once"):
        compile_source(POINT + """
        fn main() -> i32 { let p: Point = { x = 1, x = 2 }; return 0; }
        """)
