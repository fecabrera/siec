"""Lexer rules: one class per lexical form.

Each rule pairs a validator, deciding whether the rule applies at the
cursor, with a parser that consumes the form and returns its token
(or None for forms that produce no token).
"""

from siec.lexer.cursor import Cursor
from siec.lexer.token import ESCAPES, KEYWORDS, Token


class Rule:
    """
    Base class for lexer rules.
    """

    def validate(self, cursor: Cursor) -> bool:
        """
        Decide whether this rule applies at the cursor.
        """
        raise NotImplementedError

    def parse(self, cursor: Cursor) -> Token | None:
        """
        Consume the form at the cursor and return its token, if any.
        """
        raise NotImplementedError


class WhitespaceRule(Rule):
    """
    Whitespace: produces no token.
    """

    def validate(self, cursor: Cursor) -> bool:
        """
        Applies to any whitespace character.
        """
        return cursor.current().isspace()

    def parse(self, cursor: Cursor) -> None:
        """
        Consume one character, advancing the line counter on newlines.
        """
        if cursor.current() == "\n":
            cursor.line += 1
        
        cursor.advance()
        return None


class LineCommentRule(Rule):
    """
    '//' comments: skipped up to the end of the line, producing no token.
    """

    def validate(self, cursor: Cursor) -> bool:
        """
        Applies at '//'.
        """
        return cursor.starts_with("//")

    def parse(self, cursor: Cursor) -> None:
        """
        Skip to the newline, leaving it for the whitespace rule to count.
        """
        end = cursor.source.find("\n", cursor.pos)
        cursor.pos = len(cursor.source) if end == -1 else end
        return None


class MultilineCommentRule(Rule):
    """
    '/* */' comments: skipped, counting the newlines inside.
    """

    def validate(self, cursor: Cursor) -> bool:
        """
        Applies at '/*'.
        """
        return cursor.starts_with("/*")

    def parse(self, cursor: Cursor) -> None:
        """
        Skip to the first '*/', erroring if the comment never closes.
        """
        end = cursor.source.find("*/", cursor.pos + 2)
        if end == -1:
            raise SyntaxError(f"line {cursor.line}: unterminated multiline comment")

        cursor.line += cursor.source.count("\n", cursor.pos, end)
        cursor.pos = end + 2
        return None


class SymbolRule(Rule):
    """
    Symbols: multi-character symbols are matched before single characters.
    """

    MULTI = ("**=", "<<=", ">>=", "...",
             "->", "==", "!=", "<=", ">=", "**", "<<", ">>",
             "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=")
    SINGLE = "(){}[];,:.+-*/%@=<>&|^~"

    def validate(self, cursor: Cursor) -> bool:
        """
        Applies at any multi- or single-character symbol.
        """
        return (any(cursor.starts_with(sym) for sym in self.MULTI)
                or cursor.current() in self.SINGLE)

    def parse(self, cursor: Cursor) -> Token:
        """
        Consume the longest symbol at the cursor.
        """
        for sym in self.MULTI:
            if cursor.starts_with(sym):
                cursor.advance(len(sym))
                return Token("sym", sym, cursor.line)

        sym = cursor.current()
        cursor.advance()
        return Token("sym", sym, cursor.line)


class StringRule(Rule):
    """
    String literals: characters between double quotes, decoding escapes.
    """

    def validate(self, cursor: Cursor) -> bool:
        """
        Applies at a double quote.
        """
        return cursor.current() == '"'

    OCTAL = "01234567"
    HEX = "0123456789abcdefABCDEF"

    def parse(self, cursor: Cursor) -> Token:
        """
        Collect characters up to the closing quote, decoding escape sequences.
        """
        source, line = cursor.source, cursor.line
        j = cursor.pos + 1
        chars = []

        while j < len(source) and source[j] not in '"\n':
            if source[j] == "\\":
                char, j = self.read_escape(source, j + 1, line)
                chars.append(char)
            else:
                chars.append(source[j])
                j += 1

        if j >= len(source) or source[j] != '"':
            raise SyntaxError(f"line {line}: unterminated string literal")

        cursor.pos = j + 1
        return Token("str", "".join(chars), line)

    def read_escape(self, source: str, j: int, line: int) -> tuple[str, int]:
        """
        Decode one escape sequence starting after the backslash.
        """
        esc = source[j] if j < len(source) else ""

        # simple one-character escapes
        if esc in ESCAPES:
            return ESCAPES[esc], j + 1

        # octal: one to three octal digits
        if esc in self.OCTAL:
            end = j + 1
            while end < j + 3 and end < len(source) and source[end] in self.OCTAL:
                end += 1

            value = int(source[j:end], 8)
            if value > 0xFF:
                raise SyntaxError(f"line {line}: octal escape sequence out of range")

            return chr(value), end

        # hex: '\x' followed by one or more hex digits
        if esc == "x":
            end = j + 1
            while end < len(source) and source[end] in self.HEX:
                end += 1

            if end == j + 1:
                raise SyntaxError(f"line {line}: \\x used with no following hex digits")

            value = int(source[j + 1:end], 16)
            if value > 0xFF:
                raise SyntaxError(f"line {line}: hex escape sequence out of range")

            return chr(value), end

        # universal character names: '\u' plus 4 hex digits or '\U' plus 8
        if esc in "uU":
            count = 4 if esc == "u" else 8
            digits = source[j + 1:j + 1 + count]

            if len(digits) < count or any(d not in self.HEX for d in digits):
                raise SyntaxError(f"line {line}: incomplete universal character name \\{esc}")

            value = int(digits, 16)
            if value > 0x10FFFF:
                raise SyntaxError(f"line {line}: invalid universal character \\{esc}{digits}")

            return chr(value), j + 1 + count

        raise SyntaxError(f"line {line}: unknown escape sequence \\{esc}")


class IntRule(Rule):
    """
    Numeric literals: a run of digits, continuing into a float at a '.'
    followed by more digits.
    """

    def validate(self, cursor: Cursor) -> bool:
        """
        Applies at a digit.
        """
        return cursor.current().isdigit()

    def parse(self, cursor: Cursor) -> Token:
        """
        Consume the digits, and a '.digits' fraction when one follows.
        """
        whole = cursor.take_while(str.isdigit)

        # a lone '.' stays a member access ('1.x' is invalid anyway);
        # only '.<digit>' continues the number
        src, pos = cursor.source, cursor.pos
        if src.startswith(".", pos) and src[pos + 1:pos + 2].isdigit():
            cursor.advance()
            return Token("float", f"{whole}.{cursor.take_while(str.isdigit)}", cursor.line)

        return Token("int", whole, cursor.line)


class WordRule(Rule):
    """
    Words: a run of word characters, split into keywords and identifiers.
    """

    def validate(self, cursor: Cursor) -> bool:
        """
        Applies at a letter or underscore.
        """
        c = cursor.current()
        return c.isalpha() or c == "_"

    def parse(self, cursor: Cursor) -> Token:
        """
        Consume the word and classify it as a keyword or identifier.
        """
        word = cursor.take_while(lambda c: c.isalnum() or c == "_")
        return Token("kw" if word in KEYWORDS else "ident", word, cursor.line)


# the rules tried in order at each cursor position
RULES = [
    WhitespaceRule(),
    LineCommentRule(),
    MultilineCommentRule(),
    SymbolRule(),
    StringRule(),
    IntRule(),
    WordRule(),
]
