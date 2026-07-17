"""Tests for source-line tagging of compile errors."""

import pytest

from siec.codegen import codegen
from siec.codegen.errors import source_location
from siec.lexer import lex
from siec.parser import parse


def test_source_location_tags_the_error():
    """
    An error raised inside the context gains 'sie_line' and 'sie_file'.
    """
    with pytest.raises(TypeError) as info:
        with source_location(line=12, file="a.sie"):
            raise TypeError("boom")

    assert info.value.sie_line == 12
    assert info.value.sie_file == "a.sie"


def test_source_location_keeps_the_innermost_line():
    """
    A nested context does not overwrite a line already attached.
    """
    with pytest.raises(NameError) as info:
        with source_location(line=20):
            with source_location(line=5):
                raise NameError("inner")

    assert info.value.sie_line == 5


def test_source_location_fills_file_from_the_outer_context():
    """
    A statement supplies the line while the enclosing function supplies the file.
    """
    with pytest.raises(TypeError) as info:
        with source_location(line=8, file="mod.sie"):
            with source_location(line=3):
                raise TypeError("inner")

    assert info.value.sie_line == 3
    assert info.value.sie_file == "mod.sie"


def test_source_location_ignores_a_zero_line():
    """
    A zero line (an unlocated node) leaves the error untagged.
    """
    with pytest.raises(TypeError) as info:
        with source_location(line=0):
            raise TypeError("boom")

    assert getattr(info.value, "sie_line", None) is None


def compile_source(source):
    """
    Lower source text to a module.
    """
    return codegen(parse(lex(source)), "m")


def test_statement_error_carries_its_line():
    """
    A codegen error from a statement carries that statement's source line.
    """
    source = "fn main() -> i32 {\n    return missing;\n}\n"
    with pytest.raises(NameError) as info:
        compile_source(source)

    assert info.value.sie_line == 2


def test_error_reports_the_statement_not_the_function():
    """
    The tagged line is the offending statement's, not the function header's.
    """
    source = "fn main() -> i32 {\n    let a: i32 = 0;\n    let b: u32 = a;\n    return 0;\n}\n"
    with pytest.raises(TypeError) as info:
        compile_source(source)

    assert info.value.sie_line == 3
