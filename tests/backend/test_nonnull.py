"""Feature tests for non-null pointer contracts and flow checks."""

import pytest

from siec.codegen import codegen
from siec.lexer import lex
from siec.parser import parse


def compile_with_warning(source: str):
    """Compile with the unchecked-dereference warning enabled."""
    return codegen(
        parse(lex(source)), "m", warnings={"unchecked-dereference"})


def test_nonnull_pointer_has_pointer_layout_and_weakens(run):
    """A non-null pointer has pointer ABI and converts to a nullable pointer."""
    result = run(r"""
    fn read(pointer: !i32*) -> i32 {
        return *pointer;
    }

    fn main() -> i32 {
        let value = 42;
        let required: !i32* = &value;
        let nullable: i32* = required;
        if (@sizeof(!i32*) != @sizeof(i32*)) { return 1; }
        return read(nullable!);
    }
    """)
    assert result.returncode == 42


def test_literals_decay_to_nonnull_pointer_parameters(run):
    """Array and string literals have non-null compiler-owned backing."""
    result = run(r'''
    fn first_int(values: const !i32*) -> i32 { return values[0]; }
    fn first_char(value: const !char*) -> char { return value[0]; }

    fn main() -> i32 {
        return first_int([42, 2]) + first_char("*") as i32 - 42;
    }
    ''')
    assert result.returncode == 42


def test_general_array_does_not_decay_to_nonnull_pointer(compile_source):
    """An array value needs an explicit assertion on its data pointer."""
    with pytest.raises(
            TypeError,
            match=r"cannot implicitly convert i32\[\] to !i32\*"):
        compile_source(r'''
        fn read(values: !i32*) -> i32 { return values[0]; }
        fn pass(values: i32[]) -> i32 { return read(values); }
        fn main() -> i32 { return 0; }
        ''')


def test_null_cannot_initialize_nonnull_pointer(compile_source):
    """A null literal cannot satisfy a non-null pointer type."""
    with pytest.raises(TypeError, match="null cannot initialize"):
        compile_source(r"""
        fn main() -> i32 {
            let pointer: !i32* = null;
            return 0;
        }
        """)


def test_unproved_pointer_cannot_satisfy_nonnull_parameter(compile_source):
    """A nullable pointer needs a proof before a non-null call."""
    with pytest.raises(TypeError, match="not definitely non-null"):
        compile_source(r"""
        fn read(pointer: !i32*) -> i32 { return *pointer; }
        fn pass(pointer: i32*) -> i32 { return read(pointer); }
        fn main() -> i32 { return 0; }
        """)


def test_checks_and_early_returns_prove_nonnull_calls(run):
    """Branches, early returns, truth tests, and short circuiting refine pointers."""
    result = run(r"""
    fn read(pointer: !i32*) -> i32 { return *pointer; }

    fn checked(pointer: i32*) -> i32 {
        if (pointer == null) { return 0; }
        return read(pointer);
    }

    fn truthy(pointer: i32*) -> i32 {
        if (pointer and read(pointer) == 42) { return 42; }
        return 0;
    }

    fn main() -> i32 {
        let value = 42;
        return checked(&value) + truthy(&value) - 42;
    }
    """)
    assert result.returncode == 42


def test_postfix_check_refines_a_stable_local(run):
    """A successful postfix check refines its local on the continuing path."""
    result = run(r"""
    fn read(pointer: !i32*) -> i32 { return *pointer; }

    fn checked(pointer: i32*) -> i32 {
        pointer!;
        return read(pointer);
    }

    fn main() -> i32 {
        let value = 42;
        return checked(&value);
    }
    """)
    assert result.returncode == 42


def test_nonnull_contracts_work_in_fields_returns_and_generics(run):
    """Non-null pointer types remain intact in nested type positions."""
    result = run(r"""
    struct Holder { pointer: !i32*; }

    fn identity<T>(pointer: !T*) -> !T* { return pointer; }
    fn make(pointer: i32*) -> !i32* { return pointer!; }

    fn main() -> i32 {
        let value = 42;
        let holder: Holder = {make(&value)};
        return *identity(holder.pointer);
    }
    """)
    assert result.returncode == 42


def test_postfix_check_aborts_on_null(run):
    """A failed postfix check reports the failure and aborts."""
    result = run(r"""
    fn main() -> i32 {
        let pointer: i32* = null;
        pointer!;
        return 0;
    }
    """)
    assert result.returncode != 0
    assert "null pointer used in a non-null assertion" in result.stdout


def test_assignment_removes_a_nonnull_proof(compile_source):
    """Assignment from an unknown source resets a local pointer proof."""
    with pytest.raises(TypeError, match="not definitely non-null"):
        compile_source(r"""
        fn read(pointer: !i32*) -> i32 { return *pointer; }
        fn replace(pointer: i32*, other: i32*) -> i32 {
            if (pointer == null) { return 0; }
            pointer = other;
            return read(pointer);
        }
        fn main() -> i32 { return 0; }
        """)


def test_mutable_pointer_address_removes_a_nonnull_proof(compile_source):
    """A call that receives a pointer variable's address can replace it."""
    with pytest.raises(TypeError, match="not definitely non-null"):
        compile_source(r"""
        fn clear(pointer: i32**) { *pointer = null; }
        fn read(pointer: !i32*) -> i32 { return *pointer; }
        fn changed(pointer: i32*) -> i32 {
            if (pointer == null) { return 0; }
            clear(&pointer);
            return read(pointer);
        }
        fn main() -> i32 { return 0; }
        """)


def test_exposed_pointer_local_is_not_refined_persistently(compile_source):
    """Postfix bang does not retain a fact after the local address escapes."""
    with pytest.raises(TypeError, match="not definitely non-null"):
        compile_source(r"""
        fn read(pointer: !i32*) -> i32 { return *pointer; }
        fn changed(pointer: i32*) -> i32 {
            let address = &pointer;
            pointer!;
            return read(pointer);
        }
        fn main() -> i32 { return 0; }
        """)


def test_warning_reports_only_unproved_nullable_dereferences():
    """The optional warning ignores proven and statically non-null pointers."""
    module = compile_with_warning(r"""
    fn warned(pointer: i32*) -> i32 {
        return *pointer;
    }

    fn checked(pointer: i32*) -> i32 {
        if (pointer == null) { return 0; }
        return pointer[0];
    }

    fn required(pointer: !i32*) -> i32 {
        return *pointer;
    }

    fn main() -> i32 { return 0; }
    """)
    warnings = [item for item in module.sie_diagnostics
                if item.code == "unchecked-dereference"]
    assert len(warnings) == 1
    assert "'pointer' is not definitely non-null" in warnings[0].message


def test_warning_is_disabled_by_default(compile_source):
    """Nullable dereferences remain valid when the warning is not enabled."""
    module = compile_source(r"""
    fn read(pointer: i32*) -> i32 { return *pointer; }
    fn main() -> i32 { return 0; }
    """)
    assert not module.sie_diagnostics


def test_nonnull_contracts_are_checked_in_closures(compile_source):
    """A closure cannot pass an unproved nullable pointer as non-null."""
    with pytest.raises(TypeError, match="not definitely non-null"):
        compile_source(r"""
        fn read(pointer: !i32*) -> i32 { return *pointer; }

        fn main() -> i32 {
            let callback = (pointer: i32*) -> i32 => {
                return read(pointer);
            };
            return 0;
        }
        """)
