"""Feature tests for conditionals and truthiness."""


def test_if_runs_the_arm_when_true(run):
    """
    The arm runs when its condition is true.
    """
    source = """
    fn main() -> i32 {
        if (1 < 2) {
            return 1;
        }
        return 0;
    }
    """
    assert run(source).returncode == 1


def test_else_runs_when_false(run):
    """
    The else block runs when the condition is false.
    """
    source = """
    fn main() -> i32 {
        if (2 < 1) {
            return 1;
        } else {
            return 2;
        }
    }
    """
    assert run(source).returncode == 2


def test_else_if_chain_picks_the_matching_arm(run):
    """
    An else-if chain runs the first arm whose condition holds.
    """
    source = """
    fn classify(n: i32) -> i32 {
        if (n < 0) {
            return 1;
        } else if (n == 0) {
            return 2;
        } else {
            return 3;
        }
    }

    fn main() -> i32 {
        return classify(-5) + classify(0) * 10 + classify(9) * 100;
    }
    """
    # 1 + 2*10 + 3*100 = 321
    assert run(source).returncode == 321 % 256


def test_nonzero_integer_is_truthy(run):
    """
    A non-zero integer condition is truthy; zero is falsy.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = 5;
        let b: i32 = 0;

        let total: i32 = 0;
        if (a) {
            total = total + 1;
        }
        if (b) {
            total = total + 10;
        }
        return total;
    }
    """
    assert run(source).returncode == 1


def test_pointer_is_truthy_when_non_null(run):
    """
    A non-null pointer condition is truthy.
    """
    source = """
    fn main(argc: i32, argv: char**) -> i32 {
        if (argv) {
            return 4;
        }
        return 0;
    }
    """
    assert run(source).returncode == 4
