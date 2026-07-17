"""Shared fixtures for backend feature tests: source text through the whole pipeline.

Each test hands over Sie source and observes the result — the program's exit
code and output, or the compile-time error the source is expected to raise.
The CLI and include loader are deliberately left out; source goes straight
through lex, parse, codegen, and the backend.
"""

import subprocess

import pytest

from siec.backend import compile_to_object, link
from siec.codegen import codegen
from siec.lexer import lex
from siec.parser import parse


def compile_module(source: str):
    """
    Lex, parse, and generate an LLVM module from source text.
    """
    return codegen(parse(lex(source)), "m")


@pytest.fixture
def compile_source():
    """
    Compile source text to a module, surfacing any compile-time error.
    """
    return compile_module


@pytest.fixture
def run(tmp_path):
    """
    Compile, link, and run source text, returning the completed process.

    Extra positional arguments are passed to the program as its argv.
    """
    def _run(source: str, *args: str) -> subprocess.CompletedProcess:
        obj, exe = tmp_path / "m.o", tmp_path / "m"
        compile_to_object(compile_module(source), str(obj))
        link(str(obj), str(exe))
        return subprocess.run([str(exe), *args], capture_output=True, text=True)

    return _run
