"""Tokenizer for Sie source code.

Subsystems: token (the token type and vocabularies), cursor (scan
state), rules (one class per lexical form, each pairing a validator
with a parser).
"""

from siec.lexer.cursor import Cursor
from siec.lexer.rules import RULES
from siec.lexer.token import Token


def lex(source: str) -> list[Token]:
    """
    Convert Sie source code into a list of tokens ending with an 'eof' token.
    """
    cursor = Cursor(source)
    tokens = []

    # at each position, the first rule whose validator accepts parses the next form
    while not cursor.at_end():
        rule = next((r for r in RULES if r.validate(cursor)), None)
        if rule is None:
            raise SyntaxError(f"line {cursor.line}: unexpected character {cursor.current()!r}")

        token = rule.parse(cursor)
        if token is not None:
            tokens.append(token)

    tokens.append(Token("eof", "", cursor.line))
    return tokens


__all__ = ["lex", "Token", "Cursor", "RULES"]
