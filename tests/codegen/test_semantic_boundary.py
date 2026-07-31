"""Tests for the semantic-checking boundary before LLVM lowering."""

import pytest

from siec.codegen import CodeGenerator, codegen
from siec.codegen import structs
from siec.lexer import lex
from siec.parser import parse


def test_checked_program_precedes_output_ir(monkeypatch):
    """
    Every declaration, layout, and lazy instance is checked while the output
    module and its LLVM type context are still empty.
    """
    original = structs.lower_structs
    observed = []

    def inspect_boundary(gen):
        observed.append(True)
        assert gen.semantic_complete
        assert not gen.module.globals
        assert not gen.module.context.identified_types
        assert all(info.type is None for info in gen.structs.values())
        assert gen.function_instance_states["identity<i32>"] == "checked"
        original(gen)

    monkeypatch.setattr(structs, "lower_structs", inspect_boundary)

    gen = CodeGenerator("test")
    codegen(parse(lex("""
    struct Box<T> {
        value: T;
    }

    @static let answer: i32 = 42;

    fn identity<T>(value: T) -> T {
        return value;
    }

    fn main() -> i32 {
        let box: Box<i32> = { identity(42) };
        return box.value;
    }
    """)), "test", gen=gen)

    assert observed == [True]


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            '@static let invalid: i32 = "text";',
            "cannot initialize",
        ),
        (
            """
            fn main() -> i32 {
                let value: i32 = "text";
                return value;
            }
            """,
            "cannot implicitly convert",
        ),
    ],
)
def test_semantic_errors_precede_lowering(monkeypatch, source, message):
    """An invalid declaration or body never reaches the LLVM lowerer."""
    def unexpected_lowering(_):
        raise AssertionError("LLVM lowering started before checking finished")

    monkeypatch.setattr(structs, "lower_structs", unexpected_lowering)

    gen = CodeGenerator("test")
    with pytest.raises(TypeError, match=message):
        codegen(parse(lex(source)), "test", debug=True, gen=gen)

    assert not gen.module.globals
    assert not gen.module.context.identified_types
    assert not gen.module.namedmetadata
    assert all(info.type is None for info in gen.structs.values())
