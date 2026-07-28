"""Tests for siec.codegen.generator."""

import copy
import importlib

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


def test_codegen_can_reuse_the_same_program_without_mutating_it():
    """
    Codegen's rewriting passes operate on a private AST, so one parsed
    Program can safely produce multiple modules.
    """
    tree = program("""
    @type Number = i32;

    fn identity<T>(value: T) -> T { return value; }

    fn main() -> Number {
        let value: Number = identity(7 as Number);
        return value;
    }
    """)
    original = copy.deepcopy(tree)

    first = codegen(tree, "m")
    second = codegen(tree, "m")

    assert tree == original
    assert str(first) == str(second)


def test_builtin_prelude_is_parsed_once_and_cloned(monkeypatch):
    """
    Repeated compilations reuse one parsed prelude template, while each caller
    gets a private AST that registration may safely rewrite.
    """
    generator = importlib.import_module("siec.codegen.generator")
    parser = importlib.import_module("siec.parser")
    original_parse = parser.parse
    calls = 0

    def counted_parse(tokens):
        nonlocal calls
        calls += 1
        return original_parse(tokens)

    monkeypatch.setattr(parser, "parse", counted_parse)
    generator._prelude_template.cache_clear()
    try:
        first = generator.parse_prelude()
        first.functions.clear()
        second = generator.parse_prelude()

        assert calls == 1
        assert second.functions
        assert first is not second
    finally:
        # Do not leave a template produced through a patched parser shared
        # with later tests.
        generator._prelude_template.cache_clear()


def test_codegen_merges_forward_declaration_and_definition():
    """
    A forward declaration and its definition share one function.
    """
    source = "fn f() -> i32; fn f() -> i32 { return 1; }"
    module = codegen(program(source), "m")
    assert str(module).count("f") >= 1
    assert module.get_global("f()").blocks
