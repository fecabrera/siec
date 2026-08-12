"""Attaching source location (file and line) to compile errors during codegen."""

from __future__ import annotations

import os
from collections import deque
from contextlib import contextmanager
from typing import TYPE_CHECKING

from siec.diagnostics import Diagnostic

if TYPE_CHECKING:
    from siec.codegen.generator import CodeGenerator


def display_path(path: str) -> str:
    """
    Show a source path relative to the current directory when that is shorter.
    """
    try:
        relative = os.path.relpath(path)
    except ValueError:
        return path

    return relative if len(relative) < len(path) else path


def format_diagnostic(diagnostic: Diagnostic) -> str:
    """
    Render a diagnostic as '<source> at line <n>: <severity>: <message>'.

    A location it lacks simply drops out of the line, matching the historical
    warning and error prefixes the CLI printed.
    """
    where = ""
    file, line = diagnostic.file, diagnostic.line
    if file and line:
        where = f"{display_path(file)} at line {line}: "
    elif file:
        where = f"{display_path(file)}: "
    elif line:
        where = f"line {line}: "

    return f"{where}{diagnostic.severity}: {diagnostic.message}"


def warn(gen: CodeGenerator, message: str, line: int = 0,
         file: str | None = None, *, code: str | None = None) -> None:
    """
    Record a compile-time warning on the generator.

    A warning describes code that compiles; only an error stops the build.
    Callers format the collected diagnostics for the CLI or publish them
    through the LSP — nothing is printed here.
    """
    gen.diagnostics.append(Diagnostic(
        severity="warning",
        message=message,
        file=file,
        line=line or None,
        code=code,
    ))


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


def trace_edges(gen) -> dict[tuple[str, str], tuple[str, int, str]]:
    """
    Direct calls and compiler-triggered generic instantiations in one graph.
    A real call wins when both explain the same caller/callee pair.
    """
    edges = {
        edge: (*site, "called")
        for edge, site in gen.call_sites.items()
    }
    for callee, (caller, file, line) in gen.instantiation_sites.items():
        if caller is not None:
            edges.setdefault(
                (caller, callee), (file, line, "instantiated"))

    return edges


def call_path(gen, target: str | None) -> list[tuple[str, str]]:
    """
    One direct-call path from the program entry (or another graph root) to
    target, as ``(caller, callee)`` edges. Breadth-first search produces the
    shortest useful explanation when several callers reach one instance.
    """
    if target is None:
        return []

    edges = trace_edges(gen)
    callers = {caller for caller, _ in edges}
    callees = {callee for _, callee in edges}
    roots = ["main"] if "main" in callers or target == "main" else []
    roots.extend(caller for caller in callers - callees
                 if caller not in roots and caller is not None)

    for root in roots:
        queue = deque([(root, [])])
        visited = set()

        while queue:
            current, path = queue.popleft()
            if current == target:
                return path
            if current in visited:
                continue

            visited.add(current)
            for edge in edges:
                if edge[0] == current:
                    queue.append((edge[1], [*path, edge]))

    return []


@contextmanager
def error_call_trace(gen):
    """
    Attach the active function's Sie call chain to a code-generation error.

    Generic bodies emit after their callers, so the Python stack no longer
    contains those calls; the recorded source-level call graph reconstructs
    the useful chain instead.
    """
    try:
        yield
    except (TypeError, NameError) as error:
        if getattr(error, "sie_trace", None) is None:
            path = call_path(gen, gen.current_function)
            frames = []
            if path:
                from siec.codegen.overloads import display_name

                edges = trace_edges(gen)
                for caller, callee in reversed(path):
                    file, line, kind = edges[(caller, callee)]
                    frames.append(
                        (kind, file, line, display_name(caller)))

            error.sie_trace = frames
        raise
