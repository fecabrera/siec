"""Recursive-descent parser producing a Sie AST.

Subsystems: stream (token cursor), types (type annotations),
expressions, statements, functions (declarations, definitions,
and whole programs).
"""

from siec.lexer import Token
from siec.ast import Program
from siec.parser.functions import parse_function, parse_program
from siec.parser.stream import TokenStream
from siec.diagnostics import ParseLimitError


def parse(tokens: list[Token]) -> Program:
    """
    Parse a token list into a Program AST.
    """
    try:
        return parse_program(TokenStream(tokens))
    except RecursionError:
        raise ParseLimitError(
            "source nesting exceeds the compiler's parser limit") from None


__all__ = ["parse", "parse_program", "parse_function", "TokenStream"]
