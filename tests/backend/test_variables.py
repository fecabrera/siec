"""Feature tests for variables: declaration, initialization, and assignment."""


def test_let_with_initializer(run):
    """
    A let initializer becomes the variable's value.
    """
    assert run("fn main() -> i32 { let a: i32 = 9; return a; }").returncode == 9


def test_let_then_assign(run):
    """
    A variable can be reassigned after declaration.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = 1;
        a = 42;
        return a;
    }
    """
    assert run(source).returncode == 42


def test_uninitialized_let_assigned_later(run):
    """
    A variable declared without a value can be given one before use.
    """
    source = """
    fn main() -> i32 {
        let a: i32;
        a = 7;
        return a;
    }
    """
    assert run(source).returncode == 7


def test_variables_combine_in_expressions(run):
    """
    Variables read their current value wherever they appear.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = 3;
        let b: i32 = 4;
        let c: i32 = a * b + a;
        return c;
    }
    """
    assert run(source).returncode == 15
