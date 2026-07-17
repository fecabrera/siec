"""siec — a minimal compiler for the Sie language.

Pipeline: lexer -> parser -> codegen -> backend.

Usage:
    python3 -m siec main.sie [-o main]
"""

from .codegen import codegen
from .lexer import lex
from .parser import parse

__all__ = ["lex", "parse", "codegen"]
