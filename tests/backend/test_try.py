"""Feature tests for 'try <call> except (e) { ... }'.

A 'try' is the tag check written as one expression: the call runs once,
the whole thing takes the value its result carried, and the arm takes
over where an error came back instead. The arm has no value of its own
to fall out with, so it either leaves or emits a stand-in.
"""

import pytest

# a function returning each result shape, in front of every source here
SOURCE = """
@noreturn @extern fn exit(status: i32);

fn f(n: i32) -> Result<i32, u8> {
    if (n < 0) { return Error(7); }
    return Ok(n * 2);
}

fn check(n: i32) -> Result<u8> {
    if (n < 0) { return Error(9); }
    return Ok();
}
"""


def compiles(compile_source, body: str):
    """
    Compile a main built around 'f' and 'check', surfacing any error.
    """
    return compile_source(f"{SOURCE}\nfn main() -> i32 {{{body}}}")


def test_a_try_takes_the_value_its_result_carried(run):
    """
    Where the call came back with a value, the 'try' is that value and
    the arm never runs.
    """
    source = SOURCE + """
    fn main() -> i32 {
        let value = try f(21) except (error) { exit(1); }
        return value - 42;
    }
    """
    assert run(source).returncode == 0


def test_an_arm_that_returns_leaves_the_function(run):
    """
    The arm returning is the early out, written where the call is.
    """
    source = SOURCE + """
    fn doubled(n: i32) -> i32 {
        let value = try f(n) except (error) { return -(error as i32); }
        return value;
    }

    fn main() -> i32 {
        if (doubled(21) != 42) { return 1; }
        if (doubled(-1) != -7) { return 2; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_an_arm_that_emits_stands_in_for_the_value(run):
    """
    'emit' hands the 'try' a value of the arm's own, so control carries
    on past it either way.
    """
    source = SOURCE + """
    fn defaulted(n: i32) -> i32 {
        let value = try f(n) except (error) { emit (error as i32) * 100; }
        return value;
    }

    fn main() -> i32 {
        if (defaulted(3) != 6) { return 1; }
        if (defaulted(-1) != 700) { return 2; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_an_arm_may_end_in_a_noreturn_call(run):
    """
    A call that never gives control back leaves as surely as a return.
    """
    source = SOURCE + """
    fn main() -> i32 {
        let value = try f(21) except (error) { exit(error as i32); }
        return value - 42;
    }
    """
    assert run(source).returncode == 0


def test_an_arm_may_steer_the_loop_around_it(run):
    """
    'break' and 'continue' leave too, which is what a 'try' inside a
    loop reaches for.
    """
    source = SOURCE + """
    fn main() -> i32 {
        let total = 0;
        for (let i = 0; i < 4; i += 1) {
            let value = try f(i - 1) except (error) { continue; }
            total += value;
        }

        // f doubles 0, 1, and 2; the pass over -1 never got its value
        if (total != 6) { return 1; }

        while (true) {
            let value = try f(-1) except (error) { break; }
            return 2;
        }

        return 0;
    }
    """
    assert run(source).returncode == 0


def test_an_arm_cannot_fall_out_of_its_end(compile_source):
    """
    Falling off the arm would leave the 'try' with no value to be.
    """
    with pytest.raises(TypeError, match="the 'except' arm must leave, or "
                                        "'emit' a value to stand in"):
        compiles(compile_source, """
        let value = try f(1) except (error) { let e = error; }
        return value;
        """)


def test_the_error_binds_to_the_name_the_arm_asked_for(run):
    """
    The name takes the error the result carried, at the error's own type.
    """
    source = SOURCE + """
    fn main() -> i32 {
        let value = try f(-1) except (problem) { emit problem as i32; }
        return value - 7;
    }
    """
    assert run(source).returncode == 0


def test_the_error_name_lives_only_in_its_arm(compile_source):
    """
    The binding belongs to the arm: nothing outside it sees the name.
    """
    with pytest.raises(NameError, match="undefined variable 'error'"):
        compiles(compile_source, """
        let value = try f(1) except (error) { return 1; }
        return value + (error as i32);
        """)


def test_a_try_needs_a_call(compile_source):
    """
    A result already sitting in a variable was there to be checked; a
    'try' unwraps what a call just handed back.
    """
    with pytest.raises(SyntaxError, match="'try' takes a call: the result it "
                                          "unwraps comes from a function or "
                                          "a method"):
        compiles(compile_source, """
        let res = f(1);
        let value = try res except (error) { return 1; }
        return value;
        """)


def test_a_try_needs_a_call_that_returns_a_result(compile_source):
    """
    There is nothing to unwrap where no result comes back.
    """
    with pytest.raises(TypeError, match="'try' needs a call that returns a "
                                        "Result; this one returns 'i32'"):
        compile_source(SOURCE + """
        fn plain(n: i32) -> i32 { return n; }
        fn main() -> i32 {
            let value = try plain(1) except (error) { return 1; }
            return value;
        }
        """)


def test_an_error_only_result_unwraps_to_nothing(compile_source, run):
    """
    'Result<E>' carries no value, so its 'try' stands on its own: there
    is nothing to bind it to and nothing for its arm to emit.
    """
    assert run(SOURCE + """
    fn guard(n: i32) -> i32 {
        try check(n) except (error) { return -(error as i32); }
        return 0;
    }

    fn main() -> i32 {
        if (guard(1) != 0) { return 1; }
        if (guard(-1) != -9) { return 2; }
        return 0;
    }
    """).returncode == 0

    with pytest.raises(TypeError, match="a 'Result<u8>' carries no value, so "
                                        "this 'try' has none to give"):
        compiles(compile_source, """
        let value = try check(1) except (error) { return 1; }
        return value;
        """)

    with pytest.raises(TypeError, match="nothing here takes a value: the "
                                        "result this 'try' unwraps carries "
                                        "only an error"):
        compiles(compile_source, """
        try check(1) except (error) { emit 0; }
        return 0;
        """)


def test_a_try_unwraps_a_method_call_too(run):
    """
    A method's result unwraps like a function's.
    """
    source = SOURCE + """
    struct Counter { at: i32; }
    fn Counter::init(&self) { self.at = 0; }
    fn Counter::step(&self) -> Result<i32, u8> {
        self.at += 1;
        if (self.at > 2) { return Error(3); }
        return Ok(self.at);
    }

    fn main() -> i32 {
        let counter = Counter();
        let first = try counter.step() except (error) { return 1; }
        let second = try counter.step() except (error) { return 2; }
        let third = try counter.step() except (error) { emit -(error as i32); }

        return first + second - third - 6;
    }
    """
    assert run(source).returncode == 0


def test_a_try_is_an_expression_anywhere_one_fits(run):
    """
    It has a value, so it stands wherever a value does: in a condition,
    in an argument, and inside another 'try's arm.
    """
    source = SOURCE + """
    fn twice(n: i32) -> i32 { return n * 2; }

    fn main() -> i32 {
        if ((try f(1) except (error) { emit 0; }) != 2) { return 1; }

        if (twice(try f(2) except (error) { emit 0; }) != 8) { return 2; }

        let nested = try f(-1) except (error) {
            emit try f(3) except (deeper) { emit 0; }
        }

        return nested - 6;
    }
    """
    assert run(source).returncode == 0


def test_the_arm_closes_the_statement(run):
    """
    The arm's brace ends the statement the way an if's body does, so no
    ';' follows: as a let's value, an assignment's, a return's, or on
    its own.
    """
    source = SOURCE + """
    fn pick(n: i32) -> i32 {
        return try f(n) except (error) { emit -1; }
    }

    fn main() -> i32 {
        let value = try f(1) except (error) { emit 0; }
        value = try f(2) except (error) { emit 0; }
        try check(1) except (error) { return 1; }

        return value + pick(3) + pick(-1) - 9;
    }
    """
    assert run(source).returncode == 0


def test_the_call_runs_exactly_once(run):
    """
    Both paths read one result, so a call with an effect has it once.
    """
    source = SOURCE + """
    @static let calls: i32 = 0;

    fn counted(n: i32) -> Result<i32, u8> {
        calls += 1;
        if (n < 0) { return Error(7); }
        return Ok(n);
    }

    fn main() -> i32 {
        let value = try counted(5) except (error) { emit 0; }
        let fallback = try counted(-1) except (error) { emit 0; }

        if (value != 5 or fallback != 0) { return 1; }
        return calls - 2;
    }
    """
    assert run(source).returncode == 0


def test_a_defer_still_runs_when_an_arm_leaves(run):
    """
    The arm's return is a return: the scopes it leaves flush on the way
    out like any other exit path.
    """
    source = SOURCE + """
    @static let closed: i32 = 0;

    fn close() { closed += 1; }

    fn attempt(n: i32) -> i32 {
        defer close();

        let value = try f(n) except (error) { return -1; }
        return value;
    }

    fn main() -> i32 {
        if (attempt(21) != 42) { return 1; }
        if (attempt(-1) != -1) { return 2; }

        return closed - 2;
    }
    """
    assert run(source).returncode == 0
