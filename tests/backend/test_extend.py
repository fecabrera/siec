"""Feature tests for '@extend' and array methods ('fn T[]::m')."""

import pytest

ARRAY_EQ = """
@extend T[]: Eq<T[]>;

fn T[]::eq(&self, arr: const T[]) -> bool {
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

    fn P::eq(&self, o: const &P) -> bool { return self.x == o.x; }

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

    fn Box<T>::eq(&self, v: T) -> bool { return self.value == v; }

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
    with pytest.raises(TypeError, match="missing the method 'eq'"):
        compile_source("""
        struct P { x: i32; }

        @extend P: Eq<P>;

        fn main() -> i32 { return 0; }
        """)


def test_array_extend_needs_the_template(compile_source):
    """
    '@extend T[]' checks each action has its 'T[]::m' template.
    """
    with pytest.raises(TypeError, match="missing the method 'eq'"):
        compile_source("""
        @extend T[]: Eq<T[]>;

        fn main() -> i32 { return 0; }
        """)


def test_extend_needs_a_struct(compile_source):
    """
    Extending a name that isn't a struct's is an error.
    """
    with pytest.raises(TypeError, match="does not name a struct"):
        compile_source("""
        @extend Nope: Eq<i32>;

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
