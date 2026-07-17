"""The token type and the language's lexical vocabularies."""

from dataclasses import dataclass

KEYWORDS = {"fn", "return", "let", "if", "else", "and", "or", "not", "struct",
            "true", "false", "as"}

# simple one-character escapes; octal, hex, and universal forms are decoded by StringRule
ESCAPES = {
    "a": "\a", "b": "\b", "e": "\x1b", "f": "\f", "n": "\n", "r": "\r",
    "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?",
}


@dataclass
class Token:
    """
    A lexical unit of source code with its kind, text, and line number.
    """
    kind: str  # 'kw', 'ident', 'int', 'str', 'sym', 'eof'
    value: str
    line: int
