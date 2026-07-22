"""Tests for siec.codegen.generator."""

from llvmlite import ir

from siec.codegen import codegen
from siec.codegen.generator import CodeGenerator
from siec.lexer import lex
from siec.parser import parse


def test_generator_starts_with_an_empty_module():
    """
    A new generator holds an empty named module and a zeroed string counter.
    """
    gen = CodeGenerator("mod")
    assert gen.module.name == "mod"
    assert gen.str_count == 0
    assert not list(gen.module.functions)


def program(source):
    """
    Lex and parse source into a Program AST.
    """
    return parse(lex(source))


def test_codegen_defines_functions():
    """
    codegen produces a module defining the program's functions.
    """
    module = codegen(program("fn main() -> i32 { return 0; }"), "m")
    assert "define i32" in str(module)
    assert module.get_global("main") is not None


def test_codegen_emits_declarations_without_bodies():
    """
    Body-less functions become declarations with no blocks.
    """
    module = codegen(program("@extern fn puts(s: char*) -> i32;"), "m")
    assert not module.get_global("puts").blocks


def test_codegen_declares_all_functions_before_emitting_bodies():
    """
    A call to a function defined later in the file still resolves.
    """
    source = """
    fn main() -> i32 { return helper(); }
    fn helper() -> i32 { return 7; }
    """
    module = codegen(program(source), "m")
    assert "call i32" in str(module)


def test_codegen_merges_forward_declaration_and_definition():
    """
    A forward declaration and its definition share one function.
    """
    source = "fn f() -> i32; fn f() -> i32 { return 1; }"
    module = codegen(program(source), "m")
    assert str(module).count("f") >= 1
    assert module.get_global("f()").blocks
