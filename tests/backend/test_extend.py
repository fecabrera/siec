"""Feature tests for '@extend' and array methods ('fn T[]::m')."""

import pytest

ARRAY_EQ = """
@extend T[]: Eq<T[]>;

fn T[]::eq(const &self, arr: const T[]) -> bool {
    if (self.length != arr.length)
        return false;

    for (let i: u64 = 0; i < arr.length; i += 1) {
        if (self[i] != arr[i])
            return false;
    }

    return true;
}
"""


def test_array_methods_stamp_per_element(run):
    """
    A 'T[]::m' method declares over every array, its element standing in
    for the placeholder; each element type stamps its own instance.
    """
    source = """
    fn T[]::count(&self, value: T) -> i32 {
        let n = 0;
        foreach (el : self) {
            if (el == value)
                n += 1;
        }
        return n;
    }

    fn main() -> i32 {
        let ints: i32[] = [3, 1, 3];
        let text: char[] = "hello";
        return ints.count(3) * 20 + text.count('l');
    }
    """
    assert run(source).returncode == 42


BOUNDED_HASH = """
interface Hashable {
    fn hash(const &self) -> u64;
}

@extend<T: Scalar> T[]: Hashable {
    fn hash(const &self) -> u64 {
        let total: u64 = 0;
        for (let i: u64 = 0; i < self.length; i += 1) {
            total += self[i] as u64;
        }
        return total;
    }
}
"""


def test_bounded_extension_block_claims_and_implements_together(run):
    """
    One bounded extension block gives scalar arrays both the interface
    claim and the receiver-family method that implements it.
    """
    source = BOUNDED_HASH + """
    fn read<T: Hashable>(value: T) -> u64 {
        return value.hash();
    }

    fn main() -> i32 {
        let values: i32[] = [20, 22];
        return read(values) as i32;
    }
    """
    assert run(source).returncode == 42


def test_template_block_bounds_an_extension_and_sibling_method(run):
    """One template environment covers a claim block and another method."""
    source = """
    interface Hashable {
        fn hash(const &self) -> u64;
    }

    @template<T: Scalar> {
        @extend T[]: Hashable {
            fn hash(const &self) -> u64 {
                let total: u64 = 0;
                foreach (element : self)
                    total += element as u64;
                return total;
            }
        }

        fn T[]::first(const &self) -> T {
            return self[0];
        }
    }

    fn read<T: Hashable>(value: T) -> u64 {
        return value.hash();
    }

    fn main() -> i32 {
        let values: i32[] = [20, 22];
        return read(values) as i32 + values.first() - 20;
    }
    """
    assert run(source).returncode == 42


def test_template_decorator_bounds_extensions_and_methods(run):
    """The decorator form supplies the same environment declaration-wise."""
    source = """
    interface Hashable {
        fn hash(const &self) -> u64;
    }

    @template<T: Scalar>
    @extend T[]: Hashable {
        fn hash(const &self) -> u64 { return self[0] as u64; }
    }

    @template<T: Scalar>
    fn T[]::last(const &self) -> T {
        return self[self.length - 1];
    }

    fn main() -> i32 {
        let values: i32[] = [40, 2];
        return values.hash() as i32 + values.last();
    }
    """
    assert run(source).returncode == 42


def test_template_extension_accepts_an_interface_bound(run):
    """The environment's bound may be an ordinary user interface."""
    source = """
    interface Element;

    interface Counted {
        fn count(const &self) -> u64;
    }

    struct Item: Element {}

    @template<T: Element> {
        @extend T[]: Counted {
            fn count(const &self) -> u64 { return self.length; }
        }
    }

    fn count<C: Counted>(value: C) -> u64 {
        return value.count();
    }

    fn main() -> i32 {
        let items: Item[] = [{}, {}];
        return count(items) as i32 + 40;
    }
    """
    assert run(source).returncode == 42


def test_exact_array_method_precedes_an_ineligible_bounded_family(run):
    """
    A concrete array specialization wins without satisfying a separate
    receiver family's element bound.
    """
    source = """
    interface Formattable {
        fn format(const &self) -> i32;
    }

    @extend char[]: Formattable {
        fn format(const &self) -> i32 { return 42; }
    }

    @template<T: Formattable>
    @extend T[]: Formattable {
        fn format(const &self) -> i32 { return self.length as i32; }
    }

    fn main() -> i32 {
        let text: char[] = "hello";
        return text.format();
    }
    """
    assert run(source).returncode == 42


def test_bounded_extension_block_supports_bare_receiver_families(run):
    """
    The same block form blankets matching value types themselves, not
    only constructed receiver patterns such as T[].
    """
    source = """
    interface Hashable {
        fn hash(const &self) -> u64;
    }

    @template<T: Scalar>
    @extend T: Hashable {
        fn hash(const &self) -> u64 {
            return self as u64;
        }
    }

    fn read<T: Hashable>(value: T) -> u64 {
        return value.hash();
    }

    fn main() -> i32 {
        return read(40 as u8) as i32 + read(2 as i64) as i32;
    }
    """
    assert run(source).returncode == 42


def test_bounded_bare_extension_excludes_nonmatching_receivers(
        compile_source):
    """A bare blanket method is absent when its receiver misses the bound."""
    with pytest.raises(TypeError, match="type 'Item' does not implement "
                                        "interface 'Scalar'"):
        compile_source("""
        interface Hashable {
            fn hash(const &self) -> u64;
        }

        @extend<T: Scalar> T: Hashable {
            fn hash(const &self) -> u64 { return 0; }
        }

        struct Item {}

        fn main() -> i32 {
            let item: Item = {};
            return item.hash() as i32;
        }
        """)


def test_cyclic_blanket_bounds_do_not_supply_their_own_evidence(
        compile_source):
    """Mutually guarded blanket claims fail closed instead of recursing."""
    with pytest.raises(TypeError, match="type 'Item' does not implement "
                                        "interface 'Left'"):
        compile_source("""
        interface Left;
        interface Right;

        @extend<T: Left> T: Right {}
        @extend<T: Right> T: Left {}

        struct Item {}

        fn need<T: Left>(value: T) {}

        fn main() -> i32 {
            let item: Item = {};
            need(item);
            return 0;
        }
        """)


def test_bounded_extension_excludes_nonmatching_receivers(compile_source):
    """
    A struct array gets neither a scalar-array claim nor its bounded
    receiver method.
    """
    with pytest.raises(TypeError, match="type 'Item\\[\\]' does not implement "
                                        "interface 'Hashable'"):
        compile_source(BOUNDED_HASH + """
        struct Item { value: i32; }

        fn read<T: Hashable>(value: T) -> u64 {
            return value.hash();
        }

        fn main() -> i32 {
            let values: Item[];
            return read(values) as i32;
        }
        """)

    with pytest.raises(TypeError, match="type 'Item' does not implement "
                                        "interface 'Scalar'"):
        compile_source(BOUNDED_HASH + """
        struct Item { value: i32; }

        fn main() -> i32 {
            let values: Item[];
            return values.hash() as i32;
        }
        """)


def test_concrete_extension_block_owns_its_methods(run):
    """
    A non-generic extension block is the compact form of a separate
    concrete claim and receiver method.
    """
    source = """
    interface Value {
        fn value(const &self) -> i32;
    }

    struct Number { inner: i32; }

    @extend Number: Value {
        fn value(const &self) -> i32 {
            return self.inner;
        }
    }

    fn read<T: Value>(value: T) -> i32 { return value.value(); }

    fn main() -> i32 {
        let number: Number = { 42 };
        return read(number);
    }
    """
    assert run(source).returncode == 42


def test_scalar_is_a_sealed_builtin_bound(run, compile_source):
    """
    Primitive scalar types satisfy Scalar automatically; structs cannot
    claim the compiler-owned category.
    """
    source = """
    fn widen<T: Scalar>(value: T) -> u64 { return value as u64; }

    fn main() -> i32 {
        return widen(40 as u8) as i32 + widen('b') as i32 - 96;
    }
    """
    assert run(source).returncode == 42

    with pytest.raises(TypeError, match="'Scalar' is a sealed builtin "
                                        "interface"):
        compile_source("""
        struct Pretender: Scalar {}
        fn main() -> i32 { return 0; }
        """)


def test_array_operators_desugar_through_methods(run):
    """
    '==' and '!=' on array operands reach the 'T[]::eq' method.
    """
    source = ARRAY_EQ + """
    fn main() -> i32 {
        let a: i32[] = [1, 2, 3];
        let b: i32[] = [1, 2, 3];
        let c: i32[] = [1, 2, 4];
        let s: char[] = "hi";
        return (a == b and a != c and s == "hi" and s != "ho") ? 42 : 1;
    }
    """
    assert run(source).returncode == 42


def test_array_claims_satisfy_interface_parameters(run):
    """
    An '@extend T[]' claim answers per element: an array passes where
    the substituted interface is required.
    """
    source = ARRAY_EQ + """
    fn same(a: Eq<i32[]>, b: i32[]) -> bool {
        return a.eq(b);
    }

    fn main() -> i32 {
        let x: i32[] = [1, 2];
        let y: i32[] = [1, 2];
        return same(x, y) ? 42 : 1;
    }
    """
    assert run(source).returncode == 42


def test_extend_adds_claims_to_a_struct(run):
    """
    '@extend S: Iface;' claims outside the declaration, an alias
    naming the struct too.
    """
    source = """
    struct Point { x: i32; }

    @type P = Point;

    fn P::eq(const &self, o: const &P) -> bool { return self.x == o.x; }

    @extend P: Eq<Point>;

    fn main() -> i32 {
        let a: Point = {5};
        let b: Point = {5};
        return a == b ? 42 : 1;
    }
    """
    assert run(source).returncode == 42


def test_extend_carries_to_template_instances(run):
    """
    Extending a generic struct spells its own placeholders: the claims
    carry to every instantiation, whichever side of the '@extend' it
    stamps on.
    """
    source = """
    struct Box<T> { value: T; }

    fn Box<T>::eq(const &self, v: const T) -> bool { return self.value == v; }

    @extend Box<E>: Eq<E>;

    fn main() -> i32 {
        let b: Box<i32>;
        b.value = 7;

        let c: Box<char>;
        c.value = 'x';

        return (b == 7 and b != 8 and c == 'x') ? 42 : 1;
    }
    """
    assert run(source).returncode == 42


def test_extend_conformance_is_checked(compile_source):
    """
    An '@extend' claim without the method is the conformance error the
    declaration's own claim would be.
    """
    with pytest.raises(TypeError, match=r"missing the method 'eq\(const P\) -> bool'"):
        compile_source("""
        struct P { x: i32; }

        @extend P: Eq<P>;

        fn main() -> i32 { return 0; }
        """)


def test_array_extend_needs_the_template(compile_source):
    """
    '@extend T[]' checks each action has its 'T[]::m' template.
    """
    with pytest.raises(TypeError, match=r"missing the method 'eq\(const T\[\]\) -> bool'"):
        compile_source("""
        @extend T[]: Eq<T[]>;

        fn main() -> i32 { return 0; }
        """)


def test_extend_needs_a_type(compile_source):
    """
    Extending a name that names no type at all is an error.
    """
    with pytest.raises(TypeError, match="cannot extend 'Nope': it does not "
                                        "name a struct, an enum, or a "
                                        "primitive"):
        compile_source("""
        @extend Nope: Eq<i32>;

        fn main() -> i32 { return 0; }
        """)


def test_extend_needs_a_body_to_extend(compile_source):
    """
    A bodiless struct has nothing to conform: only its pointer form is
    usable, so extending it is refused.
    """
    with pytest.raises(TypeError, match="the struct has no body to extend"):
        compile_source("""
        struct Opaque;

        @extend Opaque: Eq<i32>;

        fn main() -> i32 { return 0; }
        """)


def test_primitives_and_enums_extend(run):
    """
    A primitive, an enum, and an alias naming one all extend: their
    'fn i64::m' methods satisfy the claim, and an interface parameter
    takes them like any implementer.
    """
    source = """
    interface Tag;

    fn Tag::tag(const &self) -> i64;

    enum Color { RED = 1, BLUE = 2 }

    fn Color::tag(const &self) -> i64 { return (self as i64) * 10; }

    @extend Color: Tag;

    @type Num = i64;

    fn i64::tag(const &self) -> i64 { return self * 100; }

    @extend Num: Tag;

    fn f64::tag(const &self) -> i64 { return self as i64; }

    @extend f64: Tag;

    fn read(v: const Tag) -> i64 { return v.tag(); }

    fn main() -> i32 {
        let c = Color::BLUE;
        let n: i64 = 4;
        let f: f64 = 3.5;

        return (read(c) + read(n) + read(f)) as i32 - 423;
    }
    """
    assert run(source).returncode == 0


def test_extending_a_primitive_leaves_its_operators_alone(run):
    """
    A primitive's operators stay the machine's: claiming 'Eq<i64>' over
    'i64' declares a callable 'eq' without '==' ever routing through it.
    """
    source = """
    @extend i64: Eq<i64>;

    fn i64::eq(const &self, other: i64) -> bool {
        return false;              // never consulted by '=='
    }

    fn main() -> i32 {
        let n: i64 = 4;

        if (not (n == 4)) { return 1; }
        if (n == 5) { return 2; }
        if (n.eq(4)) { return 3; }  // the method is still callable

        return 0;
    }
    """
    assert run(source).returncode == 0


def test_a_primitive_is_named_a_type_not_a_struct(compile_source):
    """
    A failed claim calls a primitive what it is, an alias following the
    type it names.
    """
    with pytest.raises(TypeError, match="type 'Num' does not implement 'Tag'"):
        compile_source("""
        interface Tag;

        fn Tag::tag(const &self) -> i64;

        @type Num = i64;

        @extend Num: Tag;

        fn main() -> i32 { return 0; }
        """)


def test_extend_needs_an_interface(compile_source):
    """
    Claiming a struct as an interface names the mistake.
    """
    with pytest.raises(TypeError, match="a struct, not an interface"):
        compile_source("""
        struct P { x: i32; }
        struct Q { x: i32; }

        @extend P: Q;

        fn main() -> i32 { return 0; }
        """)


def test_concrete_array_extends_claim_one_element(run):
    """
    '@extend char[]' claims for exactly that array: 'char[]' passes
    where the interface is required, other elements do not.
    """
    source = ARRAY_EQ.replace("@extend T[]: Eq<T[]>;",
                              "@extend char[]: Eq<char[]>;") + """
    fn same(a: Eq<char[]>, b: char[]) -> bool {
        return a.eq(b);
    }

    fn main() -> i32 {
        let s: char[] = "hi";
        return same(s, "hi") ? 42 : 1;
    }
    """
    assert run(source).returncode == 42


def test_concrete_array_claims_exclude_other_elements(compile_source):
    """
    A 'char[]' claim leaves 'i32[]' outside the interface.
    """
    with pytest.raises(TypeError, match="'i32\\[\\]' does not implement"):
        compile_source(ARRAY_EQ.replace("@extend T[]: Eq<T[]>;",
                                        "@extend char[]: Eq<char[]>;") + """
        fn same(a: Eq<char[]>, b: char[]) -> bool {
            return a.eq(b);
        }

        fn main() -> i32 {
            let n: i32[] = [1];
            return same(n, "x") ? 1 : 0;
        }
        """)


def test_concrete_array_extends_check_their_methods(compile_source):
    """
    '@extend char[]' without the stamped method is a conformance error.
    """
    with pytest.raises(TypeError, match="type 'char\\[\\]' does not implement"):
        compile_source("""
        @extend char[]: Eq<char[]>;

        fn main() -> i32 { return 0; }
        """)
