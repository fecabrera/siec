"""The token type and the language's lexical vocabularies."""

from dataclasses import dataclass

KEYWORDS = {"fn", "return", "let", "if", "else", "while", "for", "case", "when",
            "emit", "defer", "and", "or", "not", "struct", "enum", "true", "false", "as"}


def int_value(text: str) -> int:
    """
    The value of an int token: hexadecimal with an '0x' prefix, decimal otherwise.
    """
    return int(text, 16) if text[:2].lower() == "0x" else int(text)

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

    @property
    def syntax(self) -> str | None:
        """
        The value as syntax: a string or char literal's content is data,
        never syntax, so it compares as None ('["]' must not read as '[').
        """
        return None if self.kind in ("str", "char") else self.value
