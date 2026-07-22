"""Feature tests for operator overloading: 'a + b' as 'a.add(b)'."""

import pytest

MONEY = """
struct Money : Add<Money, Money>, Add<Money, i64> {
    cents: i64;
}

fn Money::add(&self, m: const &Money) -> Money {
    let r: Money = {self.cents + m.cents};
    return r;
}

fn Money::add(&self, n: i64) -> Money {
    let r: Money = {self.cents + n * 100};
    return r;
}
"""


def test_plus_desugars_to_add(run):
    """
    'a + b' on a struct operand calls 'a.add(b)'.
    """
    source = MONEY + """
    fn main() -> i32 {
        let a: Money = {40};
        let b: Money = {2};
        let c = a + b;
        return c.cents as i32;
    }
    """
    assert run(source).returncode == 42


def test_operators_pick_among_overloads(run):
    """
    The right operand's type picks 'add's overload, literals widening in.
    """
    source = MONEY + """
    fn main() -> i32 {
        let a: Money = {1000};
        let b: Money = {50};
        let c = a + b;    // the Money overload: 1050
        let d = c + 1;    // the i64 overload: 1150
        return (d.cents - 1108) as i32;
    }
    """
    assert run(source).returncode == 42


def test_every_operator_maps_to_its_method(run):
    """
    '+', '-', '*', '/', and '%' reach add, sub, mul, div, and rem.
    """
    source = """
    struct N { v: i64; }

    fn N::add(&self, o: const &N) -> N { let r: N = {self.v + o.v}; return r; }
    fn N::sub(&self, o: const &N) -> N { let r: N = {self.v - o.v}; return r; }
    fn N::mul(&self, o: const &N) -> N { let r: N = {self.v * o.v}; return r; }
    fn N::div(&self, o: const &N) -> N { let r: N = {self.v / o.v}; return r; }
    fn N::rem(&self, o: const &N) -> N { let r: N = {self.v % o.v}; return r; }

    fn main() -> i32 {
        let a: N = {84};
        let b: N = {2};
        let sum = a + b;            // 86
        let diff = a - b;           // 82
        let prod = a * b;           // 168
        let quot = a / b;           // 42
        let rest = a % b;           // 0
        return (sum.v + diff.v + prod.v + quot.v + rest.v - 336) as i32;
    }
    """
    assert run(source).returncode == 42


def test_compound_assignment_follows(run):
    """
    'a += b' desugars through the same method: 'a = a.add(b)'.
    """
    source = MONEY + """
    fn main() -> i32 {
        let a: Money = {4100};
        a += 1;   // the i64 overload: +100
        return (a.cents / 100) as i32;
    }
    """
    assert run(source).returncode == 42


def test_result_type_follows_the_method(run):
    """
    The operator's result types as the method's return, wherever S leads.
    """
    source = """
    struct Flag : Add<bool, Flag> { on: bool; }

    fn Flag::add(&self, o: const &Flag) -> bool {
        return self.on or o.on;
    }

    fn main() -> i32 {
        let a: Flag = {true};
        let b: Flag = {false};
        let both = a + b;   // a bool
        return both ? 42 : 0;
    }
    """
    assert run(source).returncode == 42


def test_operator_chains_spill_their_temporaries(run):
    """
    A method call on an operator's result references a stack spill:
    '(a + b).total()' needs no named variable.
    """
    source = MONEY + """
    fn Money::total(&self) -> i64 {
        return self.cents;
    }

    fn main() -> i32 {
        let a: Money = {40};
        let b: Money = {2};
        return (a + b).total() as i32;
    }
    """
    assert run(source).returncode == 42


def test_operator_without_the_method_is_an_error(compile_source):
    """
    An operator on a struct without its method names what is missing.
    """
    with pytest.raises(TypeError, match="has no method 'rem'"):
        compile_source(MONEY + """
        fn main() -> i32 {
            let a: Money = {1};
            let b: Money = {2};
            let c = a % b;
            return 0;
        }
        """)


def test_claim_without_the_method_is_an_error(compile_source):
    """
    Claiming 'Add<S, T>' without a matching 'add' fails conformance.
    """
    with pytest.raises(TypeError, match="missing the method 'add'"):
        compile_source("""
        struct P : Add<P, P> { x: i32; }

        fn main() -> i32 { return 0; }
        """)


def test_claim_matches_its_own_overload(compile_source):
    """
    Each 'Add<S, T>' claim checks against the overload taking T; a claim
    no overload takes names the required shape.
    """
    with pytest.raises(TypeError, match="method 'add' must take \\(f64\\)"):
        compile_source(MONEY.replace(
            "Add<Money, Money>, Add<Money, i64>",
            "Add<Money, Money>, Add<Money, i64>, Add<Money, f64>") + """
        fn main() -> i32 { return 0; }
        """)


def test_claim_checks_the_return_type(compile_source):
    """
    A claim whose S disagrees with the method's return fails conformance.
    """
    with pytest.raises(TypeError, match="method 'add' must return 'i32'"):
        compile_source("""
        struct P : Add<i32, P> { x: i32; }

        fn P::add(&self, o: const &P) -> P {
            let r: P = {self.x + o.x};
            return r;
        }

        fn main() -> i32 { return 0; }
        """)
