"""Feature tests for the ternary operator '?:'."""

import pytest


def test_ternary_selects_by_the_condition(run):
    """
    A truthy condition takes the then arm, a falsy one the else arm.
    """
    source = """
    fn pick(n: i32) -> i32 {
        return n > 0 ? 40 : 2;
    }

    fn main() -> i32 {
        return pick(1) + pick(-1);
    }
    """
    assert run(source).returncode == 42


def test_only_the_chosen_arm_evaluates(run):
    """
    The untaken arm never runs: its side effects don't happen.
    """
    source = """
    @static let calls: i32;

    fn tracked(v: i32) -> i32 {
        calls += 1;
        return v;
    }

    fn main() -> i32 {
        let x = 1 > 0 ? tracked(41) : tracked(7);
        return x + calls; // 41 + 1
    }
    """
    assert run(source).returncode == 42


def test_arms_adapt_to_the_context(run):
    """
    Literal arms take the context's type like any literal would.
    """
    source = """
    fn main() -> i32 {
        let b: u8 = 1 > 0 ? 40 : 0;
        let f: f64 = 1 > 0 ? 2.5 : 0.5;
        return b as i32 + (f - 0.5) as i32;
    }
    """
    assert run(source).returncode == 42


def test_ternary_infers_from_its_arms(run):
    """
    'let x = c ? a : 3;' adopts the arms' type, a declared arm winning.
    """
    source = """
    fn main() -> i32 {
        let a: u64 = 42;
        let x = a > 0 ? a : 3; // u64
        return x as i32;
    }
    """
    assert run(source).returncode == 42


def test_ternary_chains_right(run):
    """
    'a ? x : b ? y : z' picks through the chain, C-style.
    """
    source = """
    fn grade(n: i32) -> i32 {
        return n > 9 ? 1 : n > 3 ? 2 : 3;
    }

    fn main() -> i32 {
        return grade(10) * 100 + grade(5) * 10 + grade(0); // 123
    }
    """
    assert run(source).returncode == 123


def test_owned_ternary_result_transfers_without_destroying_arms(run):
    """Only the joined owner destroys the selected arm's returned value."""
    source = """
    @static let destroyed: i32;

    struct Owned: Destroy { value: i32; }

    fn Owned::destroy(&self) { destroyed += 1; }
    fn make(value: i32) -> Owned { return {value}; }

    fn pick(condition: bool) -> i32 {
        let chosen = condition ? make(42) : make(7);
        if (destroyed != 0) { return 100; }
        return chosen.value;
    }

    fn main() -> i32 {
        if (pick(true) != 42 or destroyed != 1) { return 1; }
        destroyed = 0;
        let second = pick(false);
        if (second != 7) { return 2; }
        if (destroyed != 1) { return 3; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_disagreeing_arms_are_an_error(compile_source):
    """
    Both arms must produce the same type.
    """
    with pytest.raises(TypeError, match="ternary arms disagree"):
        compile_source("""
        fn main() -> i32 {
            let x = 1 > 0 ? 1 : "no";
            return 0;
        }
        """)
