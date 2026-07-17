"""Scan state shared by the lexer rules."""


class Cursor:
    """
    A position in the source text, tracking the current line number.
    """

    def __init__(self, source: str):
        """
        Start scanning at the beginning of the source.
        """
        self.source = source
        self.pos = 0
        self.line = 1

    def at_end(self) -> bool:
        """
        Return whether the whole source has been consumed.
        """
        return self.pos >= len(self.source)

    def current(self) -> str:
        """
        Return the character at the cursor.
        """
        return self.source[self.pos]

    def starts_with(self, text: str) -> bool:
        """
        Return whether the source at the cursor starts with the given text.
        """
        return self.source.startswith(text, self.pos)

    def advance(self, count: int = 1) -> None:
        """
        Move the cursor forward by count characters.
        """
        self.pos += count

    def take_while(self, predicate) -> str:
        """
        Consume and return the run of characters satisfying the predicate.
        """
        start = self.pos
        while not self.at_end() and predicate(self.current()):
            self.pos += 1
        
        return self.source[start:self.pos]
