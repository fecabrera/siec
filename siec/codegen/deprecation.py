"""Warnings and errors for uses of '@deprecated' and '@remove' functions.

Every emitted call and function reference records an edge from the
function it sits in to the one it names, so once the whole program is
emitted the call graph says which functions 'main' can reach. A use of a
deprecated function inside a reachable one warns at its source line; one
no path from 'main' arrives at stays quiet. A removed function has no
body left to reach: any use of it fails on the spot.
"""

from siec.codegen.errors import warn
from siec.codegen.generator import CodeGenerator


def note_use(gen: CodeGenerator, symbol: str) -> None:
    """
    Record that the function being emitted names another: an edge for the
    reachability walk, and, when the callee is deprecated, the use itself.

    A removed callee stops the build here, with the advice it declared.
    """
    check_removed(gen, symbol)

    caller = gen.current_function
    if gen.runtime_type_guard is None:
        gen.call_graph.setdefault(caller, set()).add(symbol)
    else:
        gen.conditional_call_graph.setdefault(
            (caller, symbol), set()).add(gen.runtime_type_guard)
    if caller is not None:
        gen.call_sites.setdefault(
            (caller, symbol), (gen.current_file, gen.current_line))

    if symbol in gen.deprecated:
        gen.deprecated_uses.append((caller, symbol, gen.current_line,
                                    gen.current_file))


def check_removed(gen: CodeGenerator, symbol: str) -> None:
    """
    Fail on a use of a '@remove' function, quoting its advice.
    """
    from siec.codegen.overloads import display_name

    if (advice := gen.removed.get(symbol)) is not None:
        name = display_name(symbol)

        # an array's methods register under the one family: spell it back
        # the way it was declared
        if name.startswith("[]::"):
            name = f"T{name}"

        raise TypeError(f"{name!r} was removed: {advice}")


def check_removed_method(gen: CodeGenerator, receiver_type: str | None,
                         method: str) -> None:
    """
    Fail on a use of a removed method, whether it was declared on the
    receiver's own name or, for a generic struct or an array, on the
    template the receiver instantiates.
    """
    from siec.codegen.generics import split_generic
    from siec.codegen.types import strip_const, strip_reference

    base = strip_const(strip_reference(receiver_type or ""))
    if not base:
        return

    check_removed(gen, f"{base}::{method}")

    parts = split_generic(base)
    if parts is None and base.endswith("[]"):
        parts = ("[]", [base[:-2]])

    if parts is not None:
        check_removed(gen, f"{parts[0]}::{method}")


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


def reachable_application_functions(gen: CodeGenerator, entry: str) -> set:
    """
    Functions an application can call, including runtime interface arms for
    the concrete types a reachable expression can wrap in ``Any``.

    Interface ``when`` arms are checked for every known implementation. Their
    calls only become live when the program can actually place that concrete
    type in an ``Any`` value.
    """
    reached = {entry}
    stack = [entry]
    any_types = set(gen.any_types.get(None, ()))

    while stack:
        caller = stack.pop()
        any_types.update(gen.any_types.get(caller, ()))

        for callee in gen.call_graph.get(caller, ()):
            if callee not in reached:
                reached.add(callee)
                stack.append(callee)

        for (owner, callee), guards in gen.conditional_call_graph.items():
            if owner == caller and guards & any_types and callee not in reached:
                reached.add(callee)
                stack.append(callee)

        # A newly reached producer can enable an arm in a function already
        # visited. Revisit the graph whenever the runtime type set grows.
        expanded = set(any_types)
        for owner in reached:
            expanded.update(gen.any_types.get(owner, ()))
        if expanded != any_types:
            any_types = expanded
            stack.extend(reached)

    gen.live_any_types = any_types
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
        message = f"{display_name(symbol)!r} is deprecated"
        if advice := gen.deprecated[symbol]:
            message += f": {advice}"
        warn(gen, message, line, file, code="deprecated")
