"""Warnings for uses of '@deprecated' functions.

Every emitted call and function reference records an edge from the
function it sits in to the one it names, so once the whole program is
emitted the call graph says which functions 'main' can reach. A use of a
deprecated function inside a reachable one warns at its source line; one
no path from 'main' arrives at stays quiet.
"""

from siec.codegen.errors import warn
from siec.codegen.generator import CodeGenerator


def note_use(gen: CodeGenerator, symbol: str) -> None:
    """
    Record that the function being emitted names another: an edge for the
    reachability walk, and, when the callee is deprecated, the use itself.
    """
    caller = gen.current_function
    gen.call_graph.setdefault(caller, set()).add(symbol)

    if symbol in gen.deprecated:
        gen.deprecated_uses.append((caller, symbol, gen.current_line,
                                    gen.current_file))


def reachable_from(gen: CodeGenerator, entry: str) -> set:
    """
    Every function the entry point can reach, through calls and through
    the references it hands around.
    """
    reached = {entry}
    stack = [entry]
    while stack:
        for callee in gen.call_graph.get(stack.pop(), ()):
            if callee not in reached:
                reached.add(callee)
                stack.append(callee)

    return reached


def report_deprecations(gen: CodeGenerator) -> None:
    """
    Warn about each use of a deprecated function the program can reach.

    A unit with no 'main' of its own is a library: anything it defines
    may be an entry, so every use is reported. Uses inside a deprecated
    function stay quiet, an old implementation being free to lean on its
    own generation.
    """
    from siec.codegen.overloads import display_name

    if not gen.deprecated_uses:
        return

    entry = "main" if "main" in gen.call_graph else None
    reachable = reachable_from(gen, entry) if entry is not None else None

    seen = set()
    for caller, symbol, line, file in gen.deprecated_uses:
        if caller in gen.deprecated:
            continue

        if reachable is not None and caller is not None and caller not in reachable:
            continue

        # one warning per site, however many times emission passed it
        if (site := (symbol, line, file)) in seen:
            continue

        seen.add(site)
        warn(f"{display_name(symbol)!r} is deprecated: {gen.deprecated[symbol]}",
             line, file)
