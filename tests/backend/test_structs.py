"""Feature tests for structs: fields, nesting, passing, and initialization."""


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
