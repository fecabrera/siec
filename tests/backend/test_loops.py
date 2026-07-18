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


def test_for_drives_init_condition_and_step(run):
    """
    The init runs once, the condition gates each pass, and the step
    advances after each body.
    """
    source = """
    fn main() -> i32 {
        let total: i32 = 0;

        for (let i: i32 = 0; i < 10; i += 1) {
            total += i;
        }

        return total; // 0+1+...+9 = 45
    }
    """
    assert run(source).returncode == 45


def test_for_variable_ends_with_the_loop(compile_source):
    """
    The init's variable is gone after the loop.
    """
    import pytest

    source = """
    fn main() -> i32 {
        for (let i: i32 = 0; i < 3; i += 1) { }
        return i;
    }
    """
    with pytest.raises(NameError, match="undefined variable 'i'"):
        compile_source(source)


def test_for_loops_nest(run):
    """
    Nested for loops each drive their own variable.
    """
    source = """
    fn main() -> i32 {
        let total: i32 = 0;

        for (let i: i32 = 0; i < 3; i += 1) {
            for (let j: i32 = 0; j < 3; j += 1) {
                total += i * 3 + j;
            }
        }

        return total; // 0+1+...+8 = 36
    }
    """
    assert run(source).returncode == 36


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
