"""Fixed-point checking for lazily requested function instances."""

from siec.ast import Function
from siec.codegen.generator import CodeGenerator


def resolve_function_instance(gen: CodeGenerator, instance: Function, *,
                              deferred: bool = False) -> str:
    """
    Resolve one substituted instance header and queue its body for checking.

    Bounds and substitutions belong to the requesting generic or method
    family. This common step gives the resulting header a concrete function
    declaration, then records it as resolved. An overload whose body depends
    on overload selection waits until its first call activates it.
    """
    from siec.codegen.functions import resolve_function

    if gen.functions_lowered:
        raise RuntimeError(
            "LLVM emission attempted to resolve a function instance")

    gen.ungated_types += 1
    try:
        symbol = resolve_function(gen, instance)
    finally:
        gen.ungated_types -= 1

    gen.function_instance_states[symbol] = "resolved"
    if deferred:
        gen.deferred_overloads[symbol] = instance
    else:
        gen.pending_functions.append(instance)

    return symbol


def activate_function_instance(gen: CodeGenerator, symbol: str) -> None:
    """Queue a resolved overload instance when its first call selects it."""
    instance = gen.deferred_overloads.pop(symbol, None)
    if instance is not None:
        gen.pending_functions.append(instance)


def function_instance_symbol(gen: CodeGenerator, instance: Function) -> str:
    """The concrete module symbol belonging to a queued instance body."""
    from siec.codegen.overloads import overload_symbol

    return overload_symbol(
        gen,
        gen.resolve_symbol(instance.name),
        instance.params,
    )


def run_semantic_worklist(gen: CodeGenerator) -> None:
    """
    Resolve claims and check concrete function instances to a fixed point.

    Checking a body may request more function instances or instantiate a
    generic struct carrying new interface claims. Claims always take
    priority, so each round resolves their method dependencies before
    checking conformance or another body.
    """
    from siec.codegen.checking import check_function
    from siec.codegen.interfaces import resolve_conformance, run_conformance

    while (gen.pending_conformance or gen.resolved_conformance
           or gen.pending_functions):
        # conformance resolution runs between bodies, where no function is
        # active: a specialization it requests must not record the previous
        # body as its instantiation site
        gen.current_function = None
        resolve_conformance(gen)
        run_conformance(gen)

        if not gen.pending_functions:
            continue

        instance = gen.pending_functions.popleft()
        symbol = function_instance_symbol(gen, instance)
        gen.function_instance_states[symbol] = "checking"

        gen.ungated_types += 1
        try:
            check_function(gen, instance)
        finally:
            gen.ungated_types -= 1

        gen.function_instance_states[symbol] = "checked"
        gen.checked_instance_bodies.append(instance)
