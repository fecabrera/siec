"""Feature tests for 'type' aliases."""

import pytest


def test_scalar_alias_in_signatures_and_lets(run):
    """
    A scalar alias works anywhere its target does: params, returns, lets.
    """
    result = run("""
        @type id = u32;

        fn bump(x: id) -> id {
            return x + 1;
        }

        fn main() -> i32 {
            let x: id = 41;
            return bump(x) as i32;
        }
    """)
    assert result.returncode == 42


def test_array_alias(run):
    """
    An alias for an array type carries the whole fat value, length and all.
    """
    result = run("""
        @type words = i32[];

        fn sum(arr: const words) -> i32 {
            let total: i32 = 0;
            let i: u64 = 0;
            while (i < arr.length) {
                total += arr[i];
                i += 1;
            }
            return total;
        }

        fn main() -> i32 {
            let arr: words = [1, 2, 3];
            return sum(arr);
        }
    """)
    assert result.returncode == 6


def test_function_reference_alias(run):
    """
    An alias for a 'fn' type binds functions and calls through them.
    """
    result = run("""
        @type mapper = fn(i32) -> i32;

        fn double(x: i32) -> i32 {
            return x * 2;
        }

        fn apply(f: mapper, x: i32) -> i32 {
            return f(x);
        }

        fn main() -> i32 {
            let f: mapper = double;
            return apply(f, 21);
        }
    """)
    assert result.returncode == 42


def test_alias_of_alias_in_any_order(run):
    """
    An alias may target another alias declared later in the file.
    """
    result = run("""
        @type big = wide;
        @type wide = u64;

        fn main() -> i32 {
            let n: big = 42;
            return n as i32;
        }
    """)
    assert result.returncode == 42


def test_derived_pointer_and_sized_array(run):
    """
    '*' and '[N]' derive from an alias like from any base type.
    """
    result = run("""
        @type id = u32;
        @type buf = id[4];

        fn main() -> i32 {
            let arr: buf;
            arr[0] = 7;
            arr[3] = 5;
            let p: id* = &arr[0];
            return (arr[0] + arr[3] + arr.length as u32) as i32;
        }
    """)
    assert result.returncode == 16


def test_alias_in_struct_fields_and_enum_backing(run):
    """
    Struct fields and enum backings accept aliases for their types.
    """
    result = run("""
        @type id = u32;

        enum state: id { OFF = 0, ON }

        struct user {
            uid: id;
        }

        fn main() -> i32 {
            let u: user = { uid = 41 };
            return (u.uid + state::ON as u32) as i32;
        }
    """)
    assert result.returncode == 42


def test_cast_to_alias(run):
    """
    'as' casts to an alias like to its target.
    """
    result = run("""
        @type small = u8;

        fn main() -> i32 {
            let x: i32 = 300;
            return x as small as i32;
        }
    """)
    assert result.returncode == 44


def test_global_typed_by_alias(run):
    """
    A '@static let' global may be typed by an alias.
    """
    result = run("""
        @type counter = u64;

        @static let hits: counter = 42;

        fn main() -> i32 {
            return hits as i32;
        }
    """)
    assert result.returncode == 42


def test_const_flows_through_an_alias(compile_source):
    """
    A 'const' contract on an aliased array still bans mutation through it.
    """
    with pytest.raises(TypeError, match="const"):
        compile_source("""
            @type words = i32[];

            fn f(arr: const words) {
                arr[0] = 1;
            }
        """)


def test_alias_cycle_is_an_error(compile_source):
    """
    Aliases referencing each other in a loop are rejected at the declaration.
    """
    with pytest.raises(TypeError, match="type alias cycle: a -> b -> a"):
        compile_source("""
            @type a = b;
            @type b = a;
        """)


def test_duplicate_alias_is_an_error(compile_source):
    """
    The same alias name cannot be declared twice.
    """
    with pytest.raises(TypeError, match="declared more than once"):
        compile_source("""
            @type a = i32;
            @type a = u8;
        """)


def test_alias_cannot_shadow_a_builtin(compile_source):
    """
    Builtin type names cannot be redefined by an alias.
    """
    with pytest.raises(TypeError, match="shadows a builtin type"):
        compile_source("@type i32 = u8;")


def test_alias_cannot_collide_with_a_struct(compile_source):
    """
    An alias sharing a struct's name is a duplicate type declaration.
    """
    with pytest.raises(TypeError, match="declared more than once"):
        compile_source("""
            struct s { x: i32; }
            @type s = i32;
        """)


def test_deriving_from_a_modified_target_is_an_error(compile_source):
    """
    '*' or '[]' on an alias whose target carries 'const' or '&' would
    silently move where the modifier applies, so it is rejected.
    """
    with pytest.raises(TypeError, match="carries a modifier"):
        compile_source("""
            @type view = const i32[];

            fn f(v: view*) {}
        """)
