"""Tests for the Rule base class and the RULES registry."""

import pytest

from siec.lexer.cursor import Cursor
from siec.lexer.rules import (RULES, IntRule, LineCommentRule, MultilineCommentRule,
                              Rule, StringRule, SymbolRule, WhitespaceRule, WordRule)


def test_base_rule_validator_is_abstract():
    """
    The base rule's validator is not implemented.
    """
    with pytest.raises(NotImplementedError):
        Rule().validate(Cursor(""))


def test_base_rule_parser_is_abstract():
    """
    The base rule's parser is not implemented.
    """
    with pytest.raises(NotImplementedError):
        Rule().parse(Cursor(""))


def test_registry_covers_every_rule():
    """
    The registry lists one instance of each concrete rule.
    """
    assert [type(rule) for rule in RULES] == [
        WhitespaceRule, LineCommentRule, MultilineCommentRule,
        SymbolRule, StringRule, IntRule, WordRule,
    ]
