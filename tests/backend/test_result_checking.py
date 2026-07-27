"""Feature tests for the flow checking of 'Result' reads.

A result's 'value' only reads where its 'ok' tag is known true, its
'error' only where it is known false, and neither where the tag was
never checked. What the code establishes about a tag flows the way its
control does: through both arms of a decision, out of a branch that
leaves, and into the statements after it.
"""

import pytest

# a function returning a result, in front of every source here
SOURCE = """
fn f(n: i32) -> Result<i32, u8> {
    if (n < 0) { return Error(7); }
    return Ok(n);
}
"""


def compiles(compile_source, body: str):
    """
    Compile a main built around 'f', surfacing any compile-time error.
    """
    return compile_source(f"{SOURCE}\nfn main() -> i32 {{{body}}}")


def test_an_unchecked_result_reads_neither_member(compile_source):
    """
    Nothing is known about a result the moment it arrives, so both of
    the members its tag guards are out of reach.
    """
    with pytest.raises(TypeError, match="cannot read 'res.value': 'res.ok' "
                                        "is unchecked, so the result may "
                                        "hold an error"):
        compiles(compile_source, "let res = f(1); return res.value;")

    with pytest.raises(TypeError, match="cannot read 'res.error': 'res.ok' "
                                        "is unchecked, so the result may "
                                        "hold a value"):
        compiles(compile_source, "let res = f(1); return res.error as i32;")


def test_a_check_that_falls_through_settles_nothing(compile_source):
    """
    A branch both paths leave through rejoins knowing nothing: the tag
    was true on one side and false on the other, so it has to be
    checked again.
    """
    with pytest.raises(TypeError, match="cannot read 'res.value': 'res.ok' "
                                        "is unchecked"):
        compiles(compile_source, """
        let res = f(1);
        if (not res.ok) { let e = res.error; }
        return res.value;
        """)

    with pytest.raises(TypeError, match="cannot read 'res.error': 'res.ok' "
                                        "is unchecked"):
        compiles(compile_source, """
        let res = f(1);
        if (res.ok) { let v = res.value; }
        return res.error as i32;
        """)


def test_each_member_reads_only_where_its_tag_holds(compile_source):
    """
    Inside a branch the tag is settled, and the member the other way
    round names storage nobody wrote.
    """
    with pytest.raises(TypeError, match="cannot read 'res.error': 'res.ok' "
                                        "is true here, so the result holds "
                                        "a value"):
        compiles(compile_source, """
        let res = f(1);
        if (res.ok) { return res.error as i32; }
        return 0;
        """)

    with pytest.raises(TypeError, match="cannot read 'res.value': 'res.ok' "
                                        "is false here, so the result holds "
                                        "an error"):
        compiles(compile_source, """
        let res = f(1);
        if (not res.ok) { return res.value; }
        return 0;
        """)


def test_a_branch_that_leaves_settles_the_rest(run):
    """
    The early-out shape: the branch handling one side of the tag leaves,
    so everything after it stands on the other side.
    """
    source = SOURCE + """
    fn main() -> i32 {
        let bad = f(-1);
        if (bad.ok) { return 1; }

        // the branch left, so the tag is false from here
        if (bad.error != 7) { return 2; }

        let good = f(42);
        if (not good.ok) { return 3; }

        return good.value - 42;
    }
    """
    assert run(source).returncode == 0


def test_both_arms_of_a_decision_know_their_own_side(run):
    """
    An if body and its else each stand on one side of the tag.
    """
    source = SOURCE + """
    fn unwrap(res: Result<i32, u8>) -> i32 {
        if (res.ok) { return res.value; } else { return -(res.error as i32); }
    }

    fn main() -> i32 {
        if (unwrap(f(42)) != 42) { return 1; }
        if (unwrap(f(-1)) != -7) { return 2; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_a_settled_tag_survives_the_decisions_under_it(run):
    """
    A branch carries what it settled through everything nested inside
    it, and hands it on when the paths beside it all leave.
    """
    source = SOURCE + """
    fn main() -> i32 {
        let res = f(42);
        if (res.ok) {
            if (res.value > 100) { return 1; }
        } else {
            return 2;
        }

        return res.value - 42;
    }
    """
    assert run(source).returncode == 0


def test_a_conditional_read_needs_no_early_out(run):
    """
    Reading the value only where the tag holds is enough: the branch
    doesn't have to leave, since nothing after it reads a member.
    """
    source = SOURCE + """
    fn main() -> i32 {
        let value = -1;
        let res = f(42);
        if (res.ok) value = res.value;

        return value - 42;
    }
    """
    assert run(source).returncode == 0


def test_short_circuits_carry_the_check_to_their_right_side(run):
    """
    An 'and' only runs its right side where the left held, and an 'or'
    only where it didn't, so each side reads what its own half settled.
    """
    source = SOURCE + """
    fn main() -> i32 {
        let good = f(42);
        if (not (good.ok and good.value == 42)) { return 1; }
        if (not good.ok or good.value != 42) { return 2; }

        let bad = f(-1);
        if (bad.ok or bad.error != 7) { return 3; }

        return 0;
    }
    """
    assert run(source).returncode == 0


def test_a_ternary_arm_knows_its_condition(run):
    """
    A ternary picks one arm, and each stands where its condition put it.
    """
    source = SOURCE + """
    fn code(res: Result<i32, u8>) -> i32 {
        return res.ok ? res.value : res.error as i32;
    }

    fn main() -> i32 {
        return code(f(3)) + code(f(-1)) - 10;
    }
    """
    assert run(source).returncode == 0


def test_a_tag_compared_against_a_truth_reads_as_the_test(run):
    """
    'res.ok == false' and its siblings settle the tag exactly as the
    bare test does.
    """
    source = SOURCE + """
    fn main() -> i32 {
        let bad = f(-1);
        if (bad.ok == false) { return bad.error as i32 - 7; }
        return 1;
    }
    """
    assert run(source).returncode == 0


def test_a_case_arms_itself_on_the_tag(run):
    """
    A case over a tag knows as much as an if: 'when true' stands where
    the value holds, and the arms' leftovers go to the else.
    """
    source = SOURCE + """
    fn code(res: Result<i32, u8>) -> i32 {
        case (res.ok) {
        when true:
            return res.value;
        else:
            return res.error as i32;
        }
    }

    fn main() -> i32 {
        return code(f(3)) + code(f(-1)) - 10;
    }
    """
    assert run(source).returncode == 0


def test_a_noreturn_call_leaves_like_a_return(run):
    """
    A branch ending in an '@noreturn' call never comes back, so it
    settles the tag for everything after it just as a return does.
    """
    source = SOURCE + """
    @noreturn @extern fn exit(status: i32);

    fn main() -> i32 {
        let res = f(42);
        if (not res.ok) exit(1);

        return res.value - 42;
    }
    """
    assert run(source).returncode == 0


def test_a_result_reached_through_no_name_cannot_be_checked(compile_source):
    """
    A call's result is gone the moment it is read, so nothing could have
    settled its tag: it has to be named first.
    """
    with pytest.raises(TypeError, match=r"cannot read 'f\(\.\.\.\).value': "
                                        "the result is unchecked; name it "
                                        "and check its 'ok' first"):
        compiles(compile_source, "return f(1).value;")


def test_a_field_holding_a_result_checks_like_a_variable(run):
    """
    The tag is tracked by the storage it belongs to, so a result living
    in a field is checked through that field.
    """
    source = SOURCE + """
    struct Holder { res: Result<i32, u8>; }

    fn main() -> i32 {
        let held: Holder;
        held.res = f(42);
        if (not held.res.ok) { return held.res.error as i32; }

        return held.res.value - 42;
    }
    """
    assert run(source).returncode == 0


def test_writing_the_tag_settles_it(run):
    """
    A result built by hand is known as its own writes left it: assigning
    the tag a truth settles it the same way a check would.
    """
    source = SOURCE + """
    fn main() -> i32 {
        let res: Result<i32, u8>;
        res.ok = true;
        res.value = 42;

        return res.value - 42;
    }
    """
    assert run(source).returncode == 0


def test_a_constructed_result_is_known_from_its_constructor(compile_source, run):
    """
    'Ok' and 'Error' each settle the tag as they build it, so a result
    built right there reads the member it was given.
    """
    assert run("""
    fn main() -> i32 {
        let good = Ok<i32, u8>(42);
        let bad = Error<i32, u8>(7);

        return good.value - bad.error as i32 - 35;
    }
    """).returncode == 0

    with pytest.raises(TypeError, match="cannot read 'good.error': 'good.ok' "
                                        "is true here"):
        compile_source("""
        fn main() -> i32 {
            let good = Ok<i32, u8>(42);
            return good.error as i32;
        }
        """)


def test_a_copy_carries_what_is_known(run):
    """
    Copying a settled result copies the storage the tag speaks about, so
    the copy stands where the original did.
    """
    source = SOURCE + """
    fn main() -> i32 {
        let res = f(42);
        if (not res.ok) { return 1; }

        let copy = res;
        return copy.value - 42;
    }
    """
    assert run(source).returncode == 0


def test_writing_over_a_result_forgets_the_check(compile_source):
    """
    A check speaks about the value that was there: overwriting the
    storage leaves nothing known about what replaced it.
    """
    with pytest.raises(TypeError, match="cannot read 'res.value': 'res.ok' "
                                        "is unchecked"):
        compiles(compile_source, """
        let res = f(1);
        if (not res.ok) { return 1; }
        res = f(2);
        return res.value;
        """)


def test_handing_out_an_address_forgets_the_check(compile_source):
    """
    Whatever takes the address may write through it, so the check no
    longer answers for what the storage holds.
    """
    source = SOURCE + """
    fn touch(p: Result<i32, u8>*) {}

    fn main() -> i32 {
        let res = f(1);
        if (not res.ok) { return 1; }
        touch(&res);
        return res.value;
    }
    """
    with pytest.raises(TypeError, match="cannot read 'res.value': 'res.ok' "
                                        "is unchecked"):
        compile_source(source)


def test_a_loop_forgets_what_its_passes_write(compile_source):
    """
    A loop's next pass sees what the last one wrote, so a check made
    before it says nothing about the storage the body assigns.
    """
    with pytest.raises(TypeError, match="cannot read 'res.value': 'res.ok' "
                                        "is unchecked"):
        compiles(compile_source, """
        let res = f(1);
        if (not res.ok) { return 1; }
        while (res.value < 3) { res = f(2); }
        return res.value;
        """)


def test_a_result_declared_in_a_loop_checks_each_pass(run):
    """
    The early-out shape works inside a loop like anywhere else: the pass
    that leaves takes its error with it.
    """
    source = SOURCE + """
    fn main() -> i32 {
        let total = 0;
        for (let i = 0; i < 4; i += 1) {
            let res = f(i - 1);
            if (not res.ok) continue;

            total += res.value;
        }

        return total - 3;
    }
    """
    assert run(source).returncode == 0


def test_an_error_only_result_guards_its_error(compile_source, run):
    """
    'Result<E>' carries only the error, and the tag guards it the same
    way: it reads where the tag is false, nowhere else.
    """
    source = """
    fn check(n: i32) -> Result<u8> {
        if (n < 0) { return Error(7); }
        return Ok();
    }
    """

    with pytest.raises(TypeError, match="cannot read 'res.error': 'res.ok' "
                                        "is unchecked"):
        compile_source(source + """
        fn main() -> i32 { let res = check(1); return res.error as i32; }
        """)

    assert run(source + """
    fn main() -> i32 {
        let res = check(-1);
        if (res.ok) { return 1; }

        return res.error as i32 - 7;
    }
    """).returncode == 0


def test_a_parameter_is_a_result_like_any_other(compile_source):
    """
    A result arrives at a function unchecked: the caller's check spoke
    about the caller's storage, not this copy.
    """
    with pytest.raises(TypeError, match="cannot read 'res.value': 'res.ok' "
                                        "is unchecked"):
        compile_source(SOURCE + """
        fn take(res: Result<i32, u8>) -> i32 { return res.value; }
        fn main() -> i32 { return take(f(1)); }
        """)


def test_a_reference_parameter_checks_where_it_reads(run):
    """
    A '&Result' aliases its caller's storage, and the check stands on
    the reference the same way it would on a variable.
    """
    source = SOURCE + """
    fn unwrap(res: &Result<i32, u8>) -> i32 {
        if (not res.ok) { return -1; }
        return res.value;
    }

    fn main() -> i32 {
        let res = f(42);
        return unwrap(res) - 42;
    }
    """
    assert run(source).returncode == 0
