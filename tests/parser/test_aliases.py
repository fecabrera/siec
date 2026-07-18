"""Tests for 'type' alias parsing."""

import pytest

from siec.ast import TypeAlias
from siec.parser.functions import parse_alias, parse_program


def test_alias_declaration(ts):
    """
    '@type name = T;' parses to a TypeAlias with the canonical target name.
    """
    assert parse_alias(ts("@type my_type = i32;")) == TypeAlias("my_type", "i32")


def test_alias_targets_keep_their_canonical_names(ts):
    """
    Array, pointer, and function reference targets keep the same canonical
    names 'parse_type' gives them anywhere else.
    """
    assert parse_alias(ts("@type words = i32[];")).type == "i32[]"
    assert parse_alias(ts("@type name = char*;")).type == "char*"
    assert parse_alias(ts("@type thunk = fn();")).type == "fn()"
    assert parse_alias(ts("@type mapper = fn(i32) -> i32;")).type == "fn(i32)->i32"
    assert parse_alias(ts("@type view = const u8[];")).type == "const u8[]"


def test_alias_requires_equals_and_semicolon(ts):
    """
    The '=' and the closing ';' are both mandatory.
    """
    with pytest.raises(SyntaxError, match="expected '='"):
        parse_alias(ts("@type my_type i32;"))

    with pytest.raises(SyntaxError, match="expected ';'"):
        parse_alias(ts("@type my_type = i32"))


def test_program_collects_aliases(ts):
    """
    'type' declarations at the top level land in the program's aliases.
    """
    program = parse_program(ts("@type id = u32; fn main() -> i32 { return 0; }"))
    assert program.aliases == [TypeAlias("id", "u32")]
    assert len(program.functions) == 1
