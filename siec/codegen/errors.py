"""Attaching source location (file and line) to compile errors during codegen."""

from contextlib import contextmanager


@contextmanager
def source_location(line: int = 0, file: str = ""):
    """
    Tag any compile error raised in the block with a source file and line.

    File and line are attached independently, and only when not already set, so
    the innermost context that knows each wins: a statement supplies the line, and
    the enclosing function supplies the file. The exception type is preserved.
    """
    try:
        yield
    except (TypeError, NameError) as error:
        if line and getattr(error, "sie_line", None) is None:
            error.sie_line = line
        if file and getattr(error, "sie_file", None) is None:
            error.sie_file = file
        raise
