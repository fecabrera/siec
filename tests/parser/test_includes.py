"""Tests for siec.parser.includes."""

import pytest

from siec.ast import Include
from siec.parser.includes import parse_include


def test_include_directive(ts):
    """
    '@include("path")' parses to an Include node with the path.
    """
    assert parse_include(ts('@include("libc/stdio")')) == Include("libc/stdio")


def test_include_requires_a_string_path(ts):
    """
    An include path that isn't a string literal raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="expected 'str'"):
        parse_include(ts("@include(stdio)"))


def test_include_requires_parentheses(ts):
    """
    An include without parentheses raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match=r"expected '\('"):
        parse_include(ts('@include "stdio"'))
