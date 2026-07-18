"""Feature tests for 'defer' statements."""

import pytest

PRINTF = "@extern fn printf(fmt: char*, ...) -> i32;"


def test_defer_runs_before_the_return(run):
    """
    A deferred call runs on the way out of the function, after its body.
    """
    source = PRINTF + """
    fn main() -> i32 {
        defer printf("deferred\\n");
        printf("body\\n");
        return 0;
    }
    """
    assert run(source).stdout == "body\ndeferred\n"


def test_defers_run_in_reverse_order(run):
    """
    Multiple defers in one scope run last deferred first.
    """
    source = PRINTF + """
    fn main() -> i32 {
        defer printf("first\\n");
        defer printf("second\\n");
        return 0;
    }
    """
    assert run(source).stdout == "second\nfirst\n"


def test_defer_runs_after_the_return_value_is_computed(run):
    """
    The return value is fixed before deferred statements run.
    """
    source = """
    fn f() -> i32 {
        let x: i32 = 42;
        defer x = 99;
        return x;
    }

    fn main() -> i32 {
        return f();
    }
    """
    assert run(source).returncode == 42


def test_defer_runs_at_the_end_of_its_block(run):
    """
    A defer inside a block runs when the block ends, not the function.
    """
    source = PRINTF + """
    fn main() -> i32 {
        {
            defer printf("block\\n");
            printf("in\\n");
        }
        printf("after\\n");
        return 0;
    }
    """
    assert run(source).stdout == "in\nblock\nafter\n"


def test_defer_runs_each_loop_iteration(run):
    """
    A loop body is a scope per pass: its defers flush every iteration.
    """
    source = PRINTF + """
    fn main() -> i32 {
        for (let i = 0; i < 2; i += 1) {
            defer printf("end %d\\n", i);
            printf("iter %d\\n", i);
        }
        return 0;
    }
    """
    assert run(source).stdout == "iter 0\nend 0\niter 1\nend 1\n"


def test_deferred_block(run):
    """
    'defer { ... }' defers the whole block, statements and all.
    """
    source = PRINTF + """
    fn main() -> i32 {
        defer {
            printf("a\\n");
            printf("b\\n");
        }
        printf("body\\n");
        return 0;
    }
    """
    assert run(source).stdout == "body\na\nb\n"


def test_return_from_an_inner_scope_flushes_outer_defers(run):
    """
    Returning from inside an if arm runs the function's pending defers.
    """
    source = PRINTF + """
    fn main() -> i32 {
        defer printf("deferred\\n");
        if (true) {
            printf("arm\\n");
            return 0;
        }
        return 1;
    }
    """
    result = run(source)
    assert result.stdout == "arm\ndeferred\n"
    assert result.returncode == 0


def test_emit_flushes_the_block_expressions_defers(run):
    """
    'emit' leaves the block expression's scopes and runs their defers.
    """
    source = PRINTF + """
    fn main() -> i32 {
        let v: i32 = {
            defer printf("leaving\\n");
            emit 5;
        };
        printf("v = %d\\n", v);
        return 0;
    }
    """
    assert run(source).stdout == "leaving\nv = 5\n"


def test_defer_sees_later_writes_through_shared_slots(run):
    """
    A deferred statement reads the variable's value at run time, not defer time.
    """
    source = PRINTF + """
    fn main() -> i32 {
        let x: i32 = 1;
        defer printf("x = %d\\n", x);
        x = 42;
        return 0;
    }
    """
    assert run(source).stdout == "x = 42\n"


def test_a_deferred_return_is_an_error(compile_source):
    """
    A deferred statement runs on the way out already; it cannot return.
    """
    with pytest.raises(TypeError, match="deferred statement cannot return"):
        compile_source("fn main() -> i32 { defer { return 1; } return 0; }")


def test_a_deferred_emit_is_an_error(compile_source):
    """
    A deferred statement cannot emit the block it's flushed by leaving.
    """
    with pytest.raises(TypeError, match="deferred statement cannot emit"):
        compile_source("""
        fn main() -> i32 {
            let v: i32 = { defer { emit 2; } emit 5; };
            return v;
        }
        """)
