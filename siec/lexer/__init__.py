"""Tokenizer for Sie source code.

Subsystems: token (the token type and vocabularies), cursor (scan
state), rules (one class per lexical form, each pairing a validator
with a parser).
"""

from siec.lexer.cursor import Cursor
from siec.lexer.rules import RULES
from siec.lexer.token import Token


def read_asm_body(cursor: Cursor) -> Token:
    """
    Capture an '@asm' body raw: everything between the braces at the
    cursor, kept verbatim since assembly is not Sie code. Braces inside
    (an aarch64 register list, say) nest as long as they balance.
    """
    line = cursor.line
    cursor.advance()  # the opening '{'

    start, depth = cursor.pos, 1
    while depth > 0:
        if cursor.at_end():
            raise SyntaxError(f"line {line}: unterminated '@asm' body")

        char = cursor.current()
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == "\n":
            cursor.line += 1

        cursor.advance()

    return Token("asm", cursor.source[start:cursor.pos - 1], line)


def lex(source: str) -> list[Token]:
    """
    Convert Sie source code into a list of tokens ending with an 'eof' token.
    """
    cursor = Cursor(source)
    tokens = []

    # after '@' 'asm', the next '{' opens a raw assembly body rather than
    # Sie code; a ';' first means a bodiless form, ending the wait
    asm_pending = False

    # at each position, the first rule whose validator accepts parses the next form
    while not cursor.at_end():
        if asm_pending and cursor.current() == "{":
            tokens.append(read_asm_body(cursor))
            asm_pending = False
            continue

        rule = next((r for r in RULES if r.validate(cursor)), None)
        if rule is None:
            raise SyntaxError(f"line {cursor.line}: unexpected character {cursor.current()!r}")

        token = rule.parse(cursor)
        if token is not None:
            tokens.append(token)

            if (token.kind == "ident" and token.value == "asm"
                    and len(tokens) > 1 and tokens[-2].syntax == "@"):
                asm_pending = True
            elif token.syntax == ";":
                asm_pending = False

    tokens.append(Token("eof", "", cursor.line))
    return tokens


__all__ = ["lex", "Token", "Cursor", "RULES"]
