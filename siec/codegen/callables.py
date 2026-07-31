"""Collection and resolution of callable declarations."""

from siec.ast import Program
from siec.codegen.generator import CodeGenerator


def collect_callables(gen: CodeGenerator, program: Program) -> None:
    """
    Add a program's raw callable declarations to the declaration inventory.

    Collection records syntax and written names only. Receiver
    canonicalization, interface adaptation, overload identities, signatures,
    and bounds all wait until ``resolve_callables`` sees the complete active
    inventory.
    """
    if gen.callable_inventory_complete:
        raise RuntimeError(
            "callable collection continued after its inventory was frozen")

    for fn in program.functions:
        identity = id(fn)
        if identity in gen.collected_callables:
            continue

        gen.collected_callables.add(identity)
        gen.callable_declarations.append(fn)
        gen.raw_callables.setdefault(fn.name, []).append(fn)


def complete_callable_inventory(gen: CodeGenerator,
                                program: Program) -> None:
    """
    Freeze the raw callable inventory after every active branch is selected.

    Type-dependent conditionals join collection as soon as their branch
    becomes active. Requiring every resulting program declaration here keeps
    a future conditional path from silently bypassing collection.
    """
    missing = [
        fn for fn in program.functions
        if id(fn) not in gen.collected_callables
    ]
    if missing:
        raise RuntimeError(
            "active callable declarations bypassed collection")

    gen.callable_inventory_complete = True


def resolve_callables(gen: CodeGenerator) -> None:
    """
    Resolve every callable header from the frozen declaration inventory.

    Ordinary declarations enter before overrides regardless of source order,
    giving each override the complete target inventory it is checked against.
    """
    if not gen.callable_inventory_complete:
        raise RuntimeError(
            "callable resolution requires a complete declaration inventory")
    if gen.callables_resolved:
        raise RuntimeError("callable declarations were resolved more than once")

    from siec.codegen.functions import resolve_function
    from siec.codegen.generics import register_generic_function
    from siec.codegen.headers import resolve_callable_header
    from siec.codegen.interfaces import (
        adapt_interface_params,
        register_action,
    )
    from siec.codegen.methods import register_method

    for fn in sorted(
            gen.callable_declarations,
            key=lambda declaration: declaration.is_override):
        if fn.receiver is not None and fn.receiver in gen.interfaces:
            resolve_callable_header(gen, fn, interface_action=True)
            register_action(gen, fn)
            continue

        adapt_interface_params(gen, fn)
        resolve_callable_header(gen, fn)

        if fn.receiver is not None:
            register_method(gen, fn)
        elif fn.type_params is not None:
            register_generic_function(gen, fn)
        else:
            resolve_function(gen, fn)

    gen.callables_resolved = True
