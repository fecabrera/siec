"""Attaching source location (file and line) to compile errors during codegen."""

import os
import sys
from contextlib import contextmanager


def display_path(path: str) -> str:
    """
    Show a source path relative to the current directory when that is shorter.
    """
    try:
        relative = os.path.relpath(path)
    except ValueError:
        return path

    return relative if len(relative) < len(path) else path


def warn(message: str, line: int = 0, file: str | None = None) -> None:
    """
    Report a compile-time warning on stderr, located like an error is:
    '<source> at line <n>: warning: <message>'.

    A warning describes code that compiles; only an error stops the build.
    A location it lacks simply drops out of the line.
    """
    where = ""
    if file and line:
        where = f"{display_path(file)} at line {line}: "
    elif file:
        where = f"{display_path(file)}: "
    elif line:
        where = f"line {line}: "

    print(f"{where}warning: {message}", file=sys.stderr)


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
