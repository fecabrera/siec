"""Recursive-descent parser producing a Sie AST.

Subsystems: stream (token cursor), types (type annotations),
expressions, statements, functions (declarations, definitions,
and whole programs).
"""

from siec.lexer import Token
from siec.ast import Program
from siec.parser.functions import parse_function, parse_program
from siec.parser.stream import TokenStream


def parse(tokens: list[Token]) -> Program:
    """
    Parse a token list into a Program AST.
    """
    return parse_program(TokenStream(tokens))


__all__ = ["parse", "parse_program", "parse_function", "TokenStream"]
