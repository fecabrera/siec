"""Feature tests for generic structs: 'struct S<T>' and its instantiations."""

import pytest


def test_generic_struct_instantiates_per_argument_list(run):
    """
    Each 'S<args>' spelling stamps out one concrete struct; different
    spellings of the same arguments are the same type.
    """
    source = """
    struct Pair<A, B> {
        first: A;
        second: B;
    }

    fn sum(p: Pair<i32, i64>) -> i64 {
        return p.first as i64 + p.second;
    }

    fn main() -> i32 {
        let p: Pair<i32,i64> = { first = 40, second = 2 };
        return sum(p) as i32;
    }
    """
    assert run(source).returncode == 42


def test_nested_and_recursive_generics(run):
    """
    Arguments nest ('Box<Box<i32>>', splitting the lexer's '>>'), and a
    field may point at the instantiation itself.
    """
    source = """
    struct Box<T> { value: T; }

    struct Node<T> {
        value: T;
        next: Node<T>*;
    }

    fn main() -> i32 {
        let nested: Box<Box<i32>> = { { 39 } };

        let tail: Node<i32> = { value = 2, next = null };
        let head: Node<i32> = { value = 1, next = &tail };

        return nested.value.value + head.value + head.next[0].value;
    }
    """
    assert run(source).returncode == 42


def test_generic_unions_and_sizeof(run):
    """
    'union S<T>' instantiates the same way, and sizeof sees the layout.
    """
    source = """
    union Slot<T> {
        value: T;
        bits: u64;
    }

    fn main() -> i32 {
        let s: Slot<f64>;
        s.value = 1.0;

        if (sizeof(Slot<f64>) != 8 or s.bits != 0x3FF0000000000000) {
            return 0;
        }
        return 42;
    }
    """
    assert run(source).returncode == 42


def test_aliases_and_sized_arrays_of_instantiations(run):
    """
    An alias may name an instantiation, and 'S<i32>[N]' is a sized array
    of them, not a raw-array spelling.
    """
    source = """
    struct Box<T> { value: T; }

    @type intbox = Box<i32>;

    fn main() -> i32 {
        let boxes: Box<i32>[2];
        boxes[0] = { 20 };
        boxes[1] = { 21 };

        let aliased: intbox = { 1 };

        if (sizeof(intbox) != sizeof(Box<i32>)) {
            return 0;
        }
        return boxes[0].value + boxes[1].value + aliased.value;
    }
    """
    assert run(source).returncode == 42


def test_wrong_arity_is_rejected(compile_source):
    """
    An argument list must match the template's parameters.
    """
    with pytest.raises(TypeError, match="'Pair' takes 2 type arguments, got 1"):
        compile_source("""
        struct Pair<A, B> { a: A; b: B; }
        fn main() -> i32 { let p: Pair<i32>; return 0; }
        """)


def test_modifier_arguments_are_rejected(compile_source):
    """
    A 'const' or '&' argument would silently move where the modifier
    applies once substituted into a derived position.
    """
    with pytest.raises(TypeError, match="the argument carries a modifier"):
        compile_source("""
        struct Box<T> { value: T; }
        fn main() -> i32 { let b: Box<const char*>; return 0; }
        """)


def test_unknown_argument_type_is_rejected(compile_source):
    """
    Instantiating with an undeclared type names the real problem.
    """
    with pytest.raises(TypeError, match="unknown type 'wat'"):
        compile_source("""
        struct Box<T> { value: T; }
        fn main() -> i32 { let b: Box<wat>; return 0; }
        """)


def test_uninstantiated_template_costs_nothing(run):
    """
    A template no one instantiates registers no type and emits no code.
    """
    source = """
    struct Unused<T, U> { a: T; b: U; }

    fn main() -> i32 { return 42; }
    """
    assert run(source).returncode == 42


def test_generic_aliases_expand_with_their_arguments(run):
    """
    '@type cmp<T> = fn(T, T) -> bool' expands per argument list, for
    function types and struct targets alike.
    """
    source = """
    struct Box<T> { value: T; }

    @type cmp<T> = fn(T, T) -> bool;
    @type boxed<T> = Box<T>;
    @type boxptr<T> = Box<T>*;

    fn less(a: i32, b: i32) -> bool { return a < b; }

    fn pick(a: i32, b: i32, order: cmp<i32>) -> i32 {
        if (order(a, b)) { return a; }
        return b;
    }

    fn main() -> i32 {
        let b: boxed<i64> = { 40 };
        let p: boxptr<i64> = &b;
        return pick(44, 2, less) + p[0].value as i32; // 2 + 40
    }
    """
    assert run(source).returncode == 42


def test_generic_aliases_chain_and_nest(run):
    """
    A generic alias may target another, and feed a generic struct field.
    """
    source = """
    struct Box<T> { value: T; }

    @type inner<T> = Box<T>;
    @type outer<T> = inner<inner<T>>;

    struct Holder<T> {
        held: outer<T>;
    }

    fn main() -> i32 {
        let h: Holder<i32> = { { { 42 } } };
        return h.held.value.value;
    }
    """
    assert run(source).returncode == 42


def test_generic_alias_cycle_is_reported(compile_source):
    """
    A self-referential generic alias names its cycle instead of looping.
    """
    with pytest.raises(TypeError, match="type alias cycle: loop -> loop"):
        compile_source("""
        @type loop<T> = loop<T>;
        fn main() -> i32 { let x: loop<i32>; return 0; }
        """)


def test_generic_alias_arity_is_checked(compile_source):
    """
    An argument list must match the alias template's parameters.
    """
    with pytest.raises(TypeError, match="type alias 'cmp' takes 2 type "
                                        "arguments, got 1"):
        compile_source("""
        @type cmp<T, U> = fn(T, U) -> bool;
        fn main() -> i32 { let x: cmp<i32>; return 0; }
        """)


def test_mutual_generic_alias_cycle_is_rejected_at_declaration(compile_source):
    """
    A cycle among generic alias templates is an error even when nothing
    instantiates them: expansion could only loop.
    """
    with pytest.raises(TypeError, match="type alias cycle: A -> B -> A"):
        compile_source("""
        @type A<T> = B<T>;
        @type B<T> = A<T>;
        fn main() -> i32 { return 0; }
        """)
