"""Validation of generic and interface declaration headers."""

import pytest


@pytest.mark.parametrize(
    "declaration",
    [
        "fn f<T>(value: Missing) {}",
        "fn f<T>(value: T) -> Missing { return value; }",
        "fn f<T: Missing>(value: T) {}",
        "struct S<T: Missing> { value: T; }",
        "struct S<T> { value: Missing; }",
        "interface I<T: Missing>;",
        "interface I { fn f(const &self, value: Missing); }",
        "fn T[]::f(value: Missing) {}",
        "@type Alias<T> = Missing<T>;",
        "interface I; @extend<T: Missing> T: I;",
    ],
)
def test_invalid_unused_headers_are_rejected(compile_source, declaration):
    """
    Every declaration header resolves even when no call or concrete generic
    instance can make it reachable.
    """
    with pytest.raises(TypeError, match="unknown type 'Missing'"):
        compile_source(declaration)


def test_headers_resolve_against_later_declarations(compile_source):
    """
    Header resolution uses the complete type inventory rather than source
    order, while retaining each declaration's lexical parameters.
    """
    compile_source("""
    fn consume<T: Marked>(value: T, box: Box<T>) -> Alias<T> {
        return box;
    }

    interface Requirement<T> {
        fn apply(&self, value: Later, item: T) -> Box<T>;
    }

    @type Alias<T> = Box<T>;

    struct Box<T> {
        value: T;
    }

    struct Later {
        value: i32;
    }

    interface Marked;
    """)


def test_interface_bound_free_arguments_remain_inferable(compile_source):
    """
    A free argument inside an interface bound remains an inference variable,
    distinct from a misspelled bare bound.
    """
    compile_source("""
    interface Holds<T>;

    fn count(values: const Holds<T>[]) -> u64 {
        return values.length;
    }
    """)


def test_selected_conditional_headers_are_resolved(compile_source):
    """A late-selected declaration joins the same mandatory header pass."""
    with pytest.raises(TypeError, match="unknown type 'Missing'"):
        compile_source("""
        struct Gate {
            value: i32;
        }

        @if (@sizeof(Gate) == 4) {
            @type Invalid<T> = Missing<T>;
        }
        """)


def test_inactive_conditional_headers_do_not_exist(compile_source):
    """Unselected declarations own no identity and require no resolution."""
    compile_source("""
    @if (false) {
        fn invalid<T: Missing>(value: T) {}
    }
    """)


def test_generic_alias_may_preserve_an_opaque_foreign_handle(
        compile_source):
    """
    A generic alias has no representation of its own, so its target may stay
    opaque until a concrete use supplies the required pointer indirection.
    """
    compile_source("""
    struct ForeignHandle;
    @type HandleOf<T> = ForeignHandle;

    fn consume<T>(handle: HandleOf<T>*) {}
    @extern fn consume_concrete(handle: HandleOf<ForeignHandle>*);
    """)
