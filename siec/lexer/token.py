"""The token type and the language's lexical vocabularies."""

from dataclasses import dataclass

KEYWORDS = {"fn", "return", "let", "if", "else", "while", "for", "emit", "and", "or",
            "not", "struct", "true", "false", "as"}

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
        The value as syntax: a string literal's content is data, never
        syntax, so it compares as None ('["]' must not read as '[').
        """
        return None if self.kind == "str" else self.value
