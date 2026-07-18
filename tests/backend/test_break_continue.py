"""Feature tests for 'break' and 'continue'."""

import pytest


def test_break_leaves_the_loop(run):
    """
    'break' jumps past the loop's end.
    """
    result = run("""
        fn main() -> i32 {
            let total: i32 = 0;
            let i: i32 = 0;
            while (true) {
                i += 1;
                if (i > 6) break;
                total += i;
            }
            return total;
        }
    """)
    assert result.returncode == 21


def test_continue_skips_to_the_next_pass(run):
    """
    'continue' in a while jumps back to the condition.
    """
    result = run("""
        fn main() -> i32 {
            let total: i32 = 0;
            let i: i32 = 0;
            while (i < 10) {
                i += 1;
                if (i % 2 == 0) continue;
                total += i;
            }
            return total;
        }
    """)
    assert result.returncode == 25


def test_continue_in_a_for_still_runs_the_step(run):
    """
    'continue' in a for lands on the step, not the condition, so the
    loop always advances.
    """
    result = run("""
        fn main() -> i32 {
            let total: i32 = 0;
            for (let i: i32 = 0; i < 10; i += 1) {
                if (i % 2 == 0) continue;
                total += i;
            }
            return total;
        }
    """)
    assert result.returncode == 25


def test_break_targets_the_innermost_loop(run):
    """
    'break' leaves only the loop it sits in.
    """
    result = run("""
        fn main() -> i32 {
            let total: i32 = 0;
            for (let i: i32 = 0; i < 3; i += 1) {
                for (let j: i32 = 0; j < 10; j += 1) {
                    if (j == 2) break;
                    total += 10;
                }
                total += 1;
            }
            return total;
        }
    """)
    assert result.returncode == 63


def test_break_flushes_the_defers_of_the_scopes_it_leaves(run):
    """
    Leaving the loop body runs its deferred statements, innermost first.
    """
    result = run("""
        @extern fn printf(fmt: char*, ...) -> i32;

        fn main() -> i32 {
            while (true) {
                defer printf("body\\n");
                {
                    defer printf("inner\\n");
                    break;
                }
            }
            printf("end\\n");
            return 0;
        }
    """)
    assert result.stdout == "inner\nbody\nend\n"


def test_break_outside_a_loop_is_an_error(compile_source):
    """
    'break' and 'continue' need an enclosing loop.
    """
    with pytest.raises(TypeError, match="'break' outside a loop"):
        compile_source("fn main() -> i32 { break; return 0; }")

    with pytest.raises(TypeError, match="'continue' outside a loop"):
        compile_source("fn main() -> i32 { continue; return 0; }")


def test_deferred_break_is_an_error(compile_source):
    """
    A deferred statement cannot steer the loop it flushes inside of.
    """
    with pytest.raises(TypeError, match="a deferred statement cannot break"):
        compile_source("""
            fn main() -> i32 {
                while (true) {
                    defer { break; }
                }
                return 0;
            }
        """)

    with pytest.raises(TypeError, match="a deferred statement cannot continue"):
        compile_source("""
            fn main() -> i32 {
                while (true) {
                    defer { continue; }
                }
                return 0;
            }
        """)


def test_a_defers_own_loop_may_break_and_continue(run):
    """
    A loop living inside the deferred block steers itself freely.
    """
    result = run("""
        @extern fn printf(fmt: char*, ...) -> i32;

        fn main() -> i32 {
            defer {
                for (let i: i32 = 0; i < 10; i += 1) {
                    if (i == 2) break;
                    printf("defer %d\\n", i);
                }
            }
            return 0;
        }
    """)
    assert result.stdout == "defer 0\ndefer 1\n"
