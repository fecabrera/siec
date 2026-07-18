"""Token stream: the shared cursor state for the parser subsystems."""

from siec.lexer import Token


class TokenStream:
    """
    A cursor over a token list, shared by the parser subsystems.
    """

    def __init__(self, tokens: list[Token]):
        """
        Create a stream positioned at the first token.
        """
        self.tokens = tokens
        self.pos = 0

    def peek(self, offset: int = 0) -> Token:
        """
        Return a token ahead of the cursor without consuming it, clamped to 'eof'.
        """
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def next(self) -> Token:
        """
        Consume and return the current token.
        """
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, kind: str, value: str | None = None) -> Token:
        """
        Consume the current token, raising a SyntaxError if it doesn't match.
        """
        tok = self.next()
        if tok.kind != kind or (value is not None and tok.value != value):
            want = value or kind
            raise SyntaxError(f"line {tok.line}: expected {want!r}, got {tok.value!r}")
        
        return tok
