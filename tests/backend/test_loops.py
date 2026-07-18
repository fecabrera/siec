"""Feature tests for while loops."""


def test_while_loops_until_the_condition_falls(run):
    """
    The body repeats while the condition holds, checked before each pass.
    """
    source = """
    fn main() -> i32 {
        let total: i32 = 0;
        let i: i32 = 0;

        while (i < 10) {
            let doubled: i32 = i * 2; // body-local, fresh each iteration
            total += doubled;
            i += 1;
        }

        return total; // 0+2+...+18 = 90
    }
    """
    assert run(source).returncode == 90


def test_while_with_a_false_condition_never_runs(run):
    """
    A condition false from the start skips the body entirely.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = 4;

        while (a < 0) {
            a = 100;
        }

        return a;
    }
    """
    assert run(source).returncode == 4


def test_return_inside_a_while_leaves_the_function(run):
    """
    A return in the body exits the function mid-loop.
    """
    source = """
    fn main() -> i32 {
        let i: i32 = 0;

        while (i < 100) {
            if (i == 7) {
                return i;
            }
            i += 1;
        }

        return 0;
    }
    """
    assert run(source).returncode == 7


def test_long_loop_does_not_grow_the_stack(run):
    """
    A body-local declaration through millions of iterations must not
    re-allocate stack each pass.
    """
    source = """
    fn main() -> i32 {
        let n: i32 = 0;

        while (n < 10000000) {
            let x: i32 = n;
            n = x + 1;
        }

        return 42;
    }
    """
    assert run(source).returncode == 42
