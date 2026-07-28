"""Feature tests for compound assignment operators."""

import pytest


@pytest.mark.parametrize("init,op,operand,expected", [
    (10, "+=", 5, 15),
    (10, "-=", 5, 5),
    (10, "*=", 5, 50),
    (20, "/=", 4, 5),
    (10, "%=", 3, 1),
    (2, "**=", 5, 32),
    (1, "<<=", 5, 32),
    (64, ">>=", 3, 8),
    (12, "&=", 10, 8),
    (12, "|=", 3, 15),
    (12, "^=", 10, 6),
])
def test_compound_operators(run, init, op, operand, expected):
    """
    Each compound assignment updates the variable in place with the operator's result.
    """
    source = f"""
    fn main() -> i32 {{
        let a: i32 = {init};
        a {op} {operand};
        return a;
    }}
    """
    assert run(source).returncode == expected


def test_compound_desugars_to_the_binary_op(run):
    """
    'a += b' behaves as 'a = a + b', reading the current value first.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = 4;
        a *= a; // 16
        a += a; // 32
        return a;
    }
    """
    assert run(source).returncode == 32


def test_compound_assignment_on_a_struct_field(run):
    """
    Compound assignment applies to a struct field target.
    """
    source = """
    struct Counter {
        n: i32;
    }

    fn main() -> i32 {
        let c: Counter;
        c.n = 10;
        c.n += 5;
        c.n *= 2;
        return c.n; // 30
    }
    """
    assert run(source).returncode == 30


def test_compound_index_evaluates_its_target_once(run):
    """
    The address used to load and store an indexed element is stabilized:
    a side-effecting index runs once, as it does for a plain assignment.
    """
    source = """
    @static let calls: i32 = 0;

    fn next() -> u64 {
        calls += 1;
        return 0;
    }

    fn main() -> i32 {
        let values: i32[] = [40];
        values[next()] += 2;

        if (calls != 1) { return 1; }
        return values[0];
    }
    """
    assert run(source).returncode == 42


def test_compound_member_evaluates_its_base_once(run):
    """
    A reference-returning call at the root of a member target is evaluated
    once before the old value is read and the new one stored.
    """
    source = """
    @static let calls: i32 = 0;

    struct Counter { value: i32; }

    fn locate(counter: &Counter) -> &Counter {
        calls += 1;
        return counter;
    }

    fn main() -> i32 {
        let counter: Counter = { 40 };
        locate(counter).value += 2;

        if (calls != 1) { return 1; }
        return counter.value;
    }
    """
    assert run(source).returncode == 42
