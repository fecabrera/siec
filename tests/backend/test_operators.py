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
    with pytest.raises(TypeError, match=r"missing the method 'add\(const P\) -> P'"):
        compile_source("""
        struct P : Add<P, P> { x: i32; }

        fn main() -> i32 { return 0; }
        """)


def test_claim_matches_its_own_overload(compile_source):
    """
    Each 'Add<S, T>' claim checks against the overload taking T; a claim
    no overload takes names the required shape.
    """
    with pytest.raises(TypeError, match="method 'add' must take \\(const f64\\)"):
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


EQUAL = """
struct Pair : Eq<Pair>, Eq<i64> {
    a: i64;
    b: i64;
}

fn Pair::eq(const &self, o: const &Pair) -> bool {
    return self.a == o.a and self.b == o.b;
}

fn Pair::eq(const &self, n: const i64) -> bool {
    return self.a == n and self.b == n;
}
"""


def test_equality_desugars_to_eq(run):
    """
    '==' on a struct operand calls 'a.eq(b)'.
    """
    source = EQUAL + """
    fn main() -> i32 {
        let x: Pair = {1, 2};
        let y: Pair = {1, 2};
        let z: Pair = {3, 4};
        return (x == y and not (x == z)) ? 42 : 1;
    }
    """
    assert run(source).returncode == 42


def test_inequality_negates_eq(run):
    """
    'a != b' is equality's negation: 'not a.eq(b)'.
    """
    source = EQUAL + """
    fn main() -> i32 {
        let x: Pair = {1, 2};
        let y: Pair = {1, 2};
        let z: Pair = {3, 4};
        return (x != z and not (x != y)) ? 42 : 1;
    }
    """
    assert run(source).returncode == 42


def test_equality_picks_among_overloads(run):
    """
    The right operand's type picks 'eq's overload, literals widening in.
    """
    source = EQUAL + """
    fn main() -> i32 {
        let x: Pair = {7, 7};
        let y: Pair = {7, 8};
        return (x == 7 and y != 7) ? 42 : 1;
    }
    """
    assert run(source).returncode == 42


def test_equality_types_as_bool_in_conditions(run):
    """
    The desugared comparison is a bool wherever one is expected.
    """
    source = EQUAL + """
    fn main() -> i32 {
        let x: Pair = {1, 1};
        let same = x == 1;
        if (same) { return 42; }
        return 1;
    }
    """
    assert run(source).returncode == 42


def test_enum_equality_stays_native(run):
    """
    Enum operands keep their integer comparison; nothing desugars.
    """
    source = """
    enum Color { RED, BLUE }

    fn main() -> i32 {
        let c = Color::RED;
        return (c == Color::RED and c != Color::BLUE) ? 42 : 1;
    }
    """
    assert run(source).returncode == 42


def test_eq_conformance_is_checked(compile_source):
    """
    Claiming Eq<T> without the 'eq' method is a conformance error.
    """
    with pytest.raises(TypeError, match="eq"):
        compile_source("""
        struct P : Eq<P> { x: i32; }

        fn main() -> i32 { return 0; }
        """)


ORDERED = """
struct Version : Ord<Version>, Ord<i64> {
    major: i64;
}

fn Version::cmp(const &self, o: const &Version) -> i32 {
    return (self.major - o.major) as i32;
}

fn Version::cmp(const &self, n: const i64) -> i32 {
    return (self.major - n) as i32;
}
"""


def test_orderings_desugar_to_cmp(run):
    """
    '<', '>', '<=', and '>=' each compare 'cmp's sign against zero.
    """
    source = ORDERED + """
    fn main() -> i32 {
        let a: Version = {1};
        let b: Version = {2};
        let c: Version = {2};
        return (a < b and b > a and b <= c and c >= b
                and not (b < a) and not (a >= b)) ? 42 : 1;
    }
    """
    assert run(source).returncode == 42


def test_orderings_pick_among_cmp_overloads(run):
    """
    The right operand's type picks 'cmp's overload, literals widening in.
    """
    source = ORDERED + """
    fn main() -> i32 {
        let a: Version = {5};
        return (a < 7 and a > 3 and a >= 5 and a <= 5) ? 42 : 1;
    }
    """
    assert run(source).returncode == 42


def test_ord_conformance_is_checked(compile_source):
    """
    Claiming Ord<T> without the 'cmp' method is a conformance error.
    """
    with pytest.raises(TypeError, match="cmp"):
        compile_source("""
        struct P : Ord<P> { x: i32; }

        fn main() -> i32 { return 0; }
        """)


def test_compound_assignment_updates_in_place(run):
    """
    'a += b' on a type with an '<op>_assign' method calls it: the value
    updates in place, so nothing is built to assign back over it.
    """
    source = """
    @static let built: i32 = 0;

    struct Dec: Add<Dec, i64>, AddAssign<i64> {
        value: i64;
        id: i32;
    }

    fn Dec::init(&self, v: i64) {
        built += 1;                     // stands in for an allocation
        self.value = v;
        self.id = built;
    }

    fn Dec::add(&self, v: i64) -> Dec {
        return Dec(self.value + v);     // a fresh value, like a real Decimal
    }

    fn Dec::add_assign(&self, v: i64) {
        self.value += v;
    }

    fn main() -> i32 {
        let d = Dec(10);
        let before = built;

        d += 5;
        if (d.value != 15) { return 1; }
        if (built != before) { return 2; }   // no temporary was built
        if (d.id != before) { return 3; }    // and it is the same value
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_compound_assignment_falls_back_to_the_operator(run):
    """
    Without an in-place method the operator's result assigns back, so a
    type carrying only 'Add' keeps working.
    """
    source = """
    struct Plain: Add<Plain, i64> {
        value: i64;
    }

    fn Plain::add(&self, v: i64) -> Plain {
        let p: Plain;
        p.value = self.value + v;
        return p;
    }

    fn main() -> i32 {
        let p: Plain;
        p.value = 1;
        p += 41;
        if (p.value != 42) { return 1; }

        let n = 40;                     // plain numbers are untouched
        n += 2;
        return n - 42;
    }
    """
    assert run(source).returncode == 0


def test_every_compound_operator_has_an_in_place_form(run):
    """
    '+=', '-=', '*=', '/=', and '%=' each reach their own method, on a
    named target, a field, or an element.
    """
    source = """
    struct Acc: AddAssign<i64>, SubAssign<i64>, MulAssign<i64>,
                DivAssign<i64>, RemAssign<i64> {
        value: i64;
    }

    fn Acc::add_assign(&self, v: i64) { self.value += v; }
    fn Acc::sub_assign(&self, v: i64) { self.value -= v; }
    fn Acc::mul_assign(&self, v: i64) { self.value *= v; }
    fn Acc::div_assign(&self, v: i64) { self.value /= v; }
    fn Acc::rem_assign(&self, v: i64) { self.value %= v; }

    struct Holder { acc: Acc; }

    fn main() -> i32 {
        let a: Acc;
        a.value = 10;
        a += 5;  if (a.value != 15) { return 1; }
        a -= 3;  if (a.value != 12) { return 2; }
        a *= 4;  if (a.value != 48) { return 3; }
        a /= 6;  if (a.value != 8)  { return 4; }
        a %= 5;  if (a.value != 3)  { return 5; }

        let h: Holder;                  // a field target updates in place
        h.acc.value = 1;
        h.acc += 41;
        if (h.acc.value != 42) { return 6; }

        let arr: Acc[2];                // and an element
        arr[1].value = 2;
        arr[1] += 40;
        return arr[1].value as i32 - 42;
    }
    """
    assert run(source).returncode == 0


def test_assign_conformance_is_checked(compile_source):
    """
    Claiming 'AddAssign<T>' declares the shorthand's contract, checked
    like any other interface.
    """
    with pytest.raises(TypeError, match="does not implement 'AddAssign<i64>'"):
        compile_source("""
        struct S: AddAssign<i64> { value: i64; }
        fn main() -> i32 { let s: S; return 0; }
        """)
