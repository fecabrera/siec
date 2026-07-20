"""siec - a minimal compiler for the Sie language.

Pipeline: lexer -> parser -> codegen -> backend.

Usage:
    python3 -m siec main.sie [-o main]
"""

from siec.codegen import codegen
from siec.lexer import lex
from siec.parser import parse

__all__ = ["lex", "parse", "codegen"]
