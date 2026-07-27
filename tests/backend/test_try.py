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


def test_a_try_unwraps_a_result_wherever_it_sits(run):
    """
    What a 'try' takes is the result, not the call: one already sitting
    in a variable, in a field, or in an element unwraps the same way.
    """
    source = SOURCE + """
    struct Holder { res: Result<i32, u8>; }

    fn main() -> i32 {
        let res = f(21);
        let value = try res except (error) { return 1; }
        if (value != 42) { return 2; }

        let held: Holder;
        held.res = f(-1);
        if ((try held.res ?? -7) != -7) { return 3; }

        let all: Result<i32, u8>[2];
        all[0] = f(3);
        if ((try all[0] ?? 0) != 6) { return 4; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_a_stored_result_settles_its_tag_where_the_try_passes(run, compile_source):
    """
    The 'try' is the check, so what continues past it took the ok path
    and the arm stands where the tag is false: both sides can read the
    member their own side holds.
    """
    assert run(SOURCE + """
    fn main() -> i32 {
        let res = f(21);
        try res except (error) { return -(res.error as i32); }

        // the arm left, so the tag is true from here
        return res.value - 42;
    }
    """).returncode == 0

    with pytest.raises(TypeError, match="cannot read 'res.value': 'res.ok' "
                                        "is false here"):
        compiles(compile_source, """
        let res = f(1);
        try res except (error) { return res.value; }
        return 0;
        """)

    # a fallback hands control back, so the two paths meet knowing nothing
    with pytest.raises(TypeError, match="cannot read 'res.value': 'res.ok' "
                                        "is unchecked"):
        compiles(compile_source, """
        let res = f(1);
        let value = try res ?? 0;
        return res.value;
        """)


def test_a_try_needs_a_result_to_unwrap(compile_source):
    """
    There is nothing to unwrap where the expression is not a result, or
    carries no value at all.
    """
    with pytest.raises(TypeError, match="'try' takes a Result to unwrap; "
                                        "this one is 'i32'"):
        compiles(compile_source, """
        let n = 1;
        let value = try n except (error) { return 1; }
        return value;
        """)

    with pytest.raises(TypeError, match="'try' takes a Result to unwrap; "
                                        "this expression has no value"):
        compile_source(SOURCE + """
        fn side() {}
        fn main() -> i32 { try side(); return 0; }
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


def test_a_fallback_stands_in_for_the_value(run):
    """
    '?? v' is the arm with no error to name: v is what the 'try' takes
    where the call came back with an error instead.
    """
    source = SOURCE + """
    fn low() -> i32 { return 1; }
    fn high() -> i32 { return 2; }

    fn main() -> i32 {
        if ((try f(3) ?? 0) != 6) { return 1; }
        if ((try f(-1) ?? 0) != 0) { return 2; }
        if ((try f(-1) ?? low() + high()) != 3) { return 3; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_a_fallback_runs_only_where_the_tag_is_false(run):
    """
    The fallback sits on the error path, so a call inside it is spent
    only there, never on the way to a value the result already had.
    """
    source = SOURCE + """
    @static let recoveries: i32 = 0;

    fn recover() -> i32 {
        recoveries += 1;
        return -1;
    }

    fn main() -> i32 {
        if ((try f(3) ?? recover()) != 6) { return 1; }
        if (recoveries != 0) { return 2; }

        if ((try f(-1) ?? recover()) != -1) { return 3; }
        return recoveries - 1;
    }
    """
    assert run(source).returncode == 0


def test_a_braced_fallback_is_the_arm_itself(run):
    """
    Braces make the fallback an arm: statements first, then 'emit', or
    a way out instead.
    """
    source = SOURCE + """
    @static let reports: i32 = 0;

    fn report() { reports += 1; }

    fn recovered(n: i32) -> i32 {
        let value = try f(n) ?? { report(); emit -1; };
        return value;
    }

    fn early(n: i32) -> i32 {
        let value = try f(n) ?? { return -2; };
        return value;
    }

    fn main() -> i32 {
        if (recovered(3) != 6 or reports != 0) { return 1; }
        if (recovered(-1) != -1 or reports != 1) { return 2; }
        if (early(3) != 6) { return 3; }
        if (early(-1) != -2) { return 4; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_a_fallback_over_a_valueless_result_is_simply_run(run):
    """
    'Result<E>' has no value to stand in for, so the fallback runs for
    its effects and control carries on past it.
    """
    source = SOURCE + """
    @static let warnings: i32 = 0;

    fn warn() { warnings += 1; }

    fn main() -> i32 {
        try check(1) ?? warn();
        if (warnings != 0) { return 1; }

        try check(-1) ?? warn();
        if (warnings != 1) { return 2; }

        try check(-1) ?? { warn(); warn(); };
        return warnings - 3;
    }
    """
    assert run(source).returncode == 0


def test_a_valueless_arm_may_fall_out(run):
    """
    An 'except' arm owes a value only where the result carries one:
    over a 'Result<E>' there is nothing to owe, so handling the error
    and carrying on is enough.
    """
    source = SOURCE + """
    @static let seen: i32 = 0;

    fn main() -> i32 {
        try check(-1) except (error) { seen = error as i32; }
        return seen - 9;
    }
    """
    assert run(source).returncode == 0


def test_a_fallback_owing_a_value_cannot_fall_out(compile_source):
    """
    Where the result carries a value, a braced fallback is an arm like
    any other: it emits a stand-in or leaves.
    """
    with pytest.raises(TypeError, match="the fallback must leave, or 'emit' "
                                        "a value to stand in"):
        compiles(compile_source, """
        let value = try f(1) ?? { let missing = 0; };
        return value;
        """)

    with pytest.raises(TypeError, match="nothing here takes a value: the "
                                        "result this 'try' unwraps carries "
                                        "only an error"):
        compiles(compile_source, """
        try check(1) ?? { emit 0; };
        return 0;
        """)


def test_a_fallback_names_no_error(compile_source):
    """
    The shorthand trades the binding for the brevity: nothing in the
    fallback can reach the error.
    """
    with pytest.raises(NameError, match="undefined variable 'error'"):
        compiles(compile_source, """
        let value = try f(1) ?? (error as i32);
        return value;
        """)


def test_a_fallback_keeps_its_semicolon(compile_source, run):
    """
    The fallback is part of the expression, not a body around it, so a
    statement built on one closes with ';' like any other, braced or not.
    """
    assert run(SOURCE + """
    fn main() -> i32 {
        let value = try f(1) ?? 0;
        value = try f(2) ?? { emit 0; };
        try check(1) ?? { };

        return value - 4;
    }
    """).returncode == 0

    with pytest.raises(SyntaxError, match="expected ';'"):
        compiles(compile_source, """
        let value = try f(1) ?? 0
        return value;
        """)

    with pytest.raises(SyntaxError, match="expected ';'"):
        compiles(compile_source, """
        let value = try f(1) ?? { emit 0; }
        return value;
        """)


def test_a_bare_try_hands_the_error_to_the_caller(run):
    """
    With no arm at all, the error goes back the way it came: the
    function returns it, and where a value came instead the 'try' is
    that value.
    """
    source = SOURCE + """
    fn twice(n: i32) -> Result<i32, u8> {
        let value = try f(n);
        return Ok(value * 2);
    }

    fn main() -> i32 {
        let good = twice(3);
        if (not good.ok or good.value != 12) { return 1; }

        let bad = twice(-1);
        if (bad.ok or bad.error != 7) { return 2; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_a_bare_try_crosses_between_result_shapes(run):
    """
    Only the error crosses over, so a valueless result propagates into a
    function carrying a value and the other way round.
    """
    source = SOURCE + """
    fn tagged(n: i32) -> Result<u8> {
        try check(n);
        return Ok();
    }

    fn valued(n: i32) -> Result<i32, u8> {
        try check(n);           // 'Result<u8>' into 'Result<i32, u8>'
        return Ok(5);
    }

    fn discarding(n: i32) -> Result<u8> {
        try f(n);               // 'Result<i32, u8>' into 'Result<u8>'
        return Ok();
    }

    fn main() -> i32 {
        if (not tagged(1).ok) { return 1; }

        let bad = tagged(-1);
        if (bad.ok or bad.error != 9) { return 2; }

        let value = valued(1);
        if (not value.ok or value.value != 5) { return 3; }
        if (valued(-1).ok) { return 4; }

        if (not discarding(1).ok) { return 5; }

        let dropped = discarding(-1);
        if (dropped.ok or dropped.error != 7) { return 6; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_a_bare_try_needs_a_caller_that_returns_a_result(compile_source):
    """
    There is nowhere to hand the error where no result comes back.
    """
    with pytest.raises(TypeError, match="a bare 'try' hands its error back to "
                                        "the caller, so 'main' must return a "
                                        "Result; it returns 'i32'"):
        compiles(compile_source, "try check(1); return 0;")

    with pytest.raises(TypeError, match="'side' must return a Result; it "
                                        "returns nothing"):
        compile_source(SOURCE + """
        fn side() { try check(1); }
        fn main() -> i32 { side(); return 0; }
        """)


def test_a_bare_try_needs_the_same_error_type(compile_source):
    """
    The error passes on as it is, so converting it is not on offer.
    """
    with pytest.raises(TypeError, match="cannot hand a 'char' error back from "
                                        r"'wrong', which returns 'Result<u8>'"):
        compile_source(SOURCE + """
        fn other(n: i32) -> Result<i64, char> { return Error('x'); }
        fn wrong(n: i32) -> Result<u8> {
            try other(n);
            return Ok();
        }
        fn main() -> i32 { return 0; }
        """)


def test_a_bare_try_flushes_the_scopes_it_leaves(run):
    """
    Handing the error back is a return, so the scopes it leaves run
    their deferred statements on the way out.
    """
    source = SOURCE + """
    @static let closed: i32 = 0;

    fn close() { closed += 1; }

    fn attempt(n: i32) -> Result<i32, u8> {
        defer close();

        let value = try f(n);
        return Ok(value);
    }

    fn main() -> i32 {
        if (not attempt(3).ok) { return 1; }
        if (attempt(-1).ok) { return 2; }

        return closed - 2;
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
