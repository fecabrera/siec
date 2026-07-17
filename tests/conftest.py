"""Shared fixtures for the siec test suite."""

import pytest
from llvmlite import ir

from siec.codegen.generator import CodeGenerator
from siec.lexer import lex
from siec.parser.stream import TokenStream


@pytest.fixture
def ts():
    """
    Factory building a TokenStream from source text.
    """
    def make(source: str) -> TokenStream:
        return TokenStream(lex(source))
    return make


@pytest.fixture
def env():
    """
    A CodeGenerator plus a builder positioned inside an open i32 function.
    """
    gen = CodeGenerator("test")
    func = ir.Function(gen.module, ir.FunctionType(ir.IntType(32), []), name="host")
    builder = ir.IRBuilder(func.append_basic_block("entry"))

    # register the host's Sie signature so return coercion can find its type
    gen.return_types["host"] = "i32"
    gen.param_types["host"] = []

    return gen, builder
