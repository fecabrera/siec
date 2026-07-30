"""Validation and selection of concrete ``@override`` declarations."""

from siec.codegen.errors import source_location
from siec.codegen.generics import split_generic, unify
from siec.codegen.interfaces import constraints_hold
from siec.codegen.methods import method_signature
from siec.codegen.overloads import overload_symbol, shown_signature


def function_symbol(gen, fn) -> str:
    """The registered module symbol belonging to one concrete declaration."""
    previous, gen.current_file = gen.current_file, fn.file
    try:
        return overload_symbol(
            gen,
            gen.resolve_symbol(fn.name),
            fn.params,
        )
    finally:
        gen.current_file = previous


def family_method_target(gen, fn) -> bool:
    """
    Whether a concrete method override matches an ordinary receiver family.

    ``char[]::f`` can therefore override ``T[]::f`` without first declaring
    a redundant concrete forwarding signature.
    """
    if fn.receiver is None:
        return False

    base = fn.receiver
    method = fn.name.partition("::")[2]
    parts = split_generic(base)
    if parts is None and base.endswith("[]"):
        parts = ("[]", [base[:-2]])

    entries = []
    if parts is not None:
        for template in gen.generic_methods.get((parts[0], method), ()):
            mapping = dict(zip(template.receiver_params or (), parts[1]))
            entries.append((template, mapping))

    for template in gen.generic_receiver_methods.get(method, ()):
        mapping = {}
        try:
            unify(template.receiver, base, template.receiver_params, mapping)
        except TypeError:
            continue
        if all(param in mapping for param in template.receiver_params):
            entries.append((template, mapping))

    wanted = method_signature(fn)
    return any(
        not template.is_override
        and constraints_hold(
            gen, template.receiver_constraints, mapping, template.file)
        and method_signature(template, mapping) == wanted
        for template, mapping in entries
    )


def validate_concrete_overrides(gen, program) -> None:
    """
    Validate concrete overrides and mark the definitions they displace.

    Signatures remain registered for calls and navigation. Only the winning
    body emits, making replacement independent of declaration order.
    """
    groups: dict[str, list] = {}
    for fn in program.functions:
        if (fn.type_params is not None or fn.receiver_params is not None
                or fn.receiver in gen.interfaces):
            continue
        groups.setdefault(function_symbol(gen, fn), []).append(fn)

    for declarations in groups.values():
        ordinary = [fn for fn in declarations if not fn.is_override]
        overrides = [fn for fn in declarations if fn.is_override]
        if not overrides:
            continue

        for fn in overrides:
            with source_location(line=fn.line, file=fn.file):
                if not ordinary and not family_method_target(gen, fn):
                    raise TypeError(
                        f"function '{shown_signature(fn)}' has no matching "
                        "declaration to override")
                if fn.receiver is not None:
                    gen.overridden_method_signatures.setdefault(
                        fn.name, set()).add(method_signature(fn))

        definitions = [
            fn for fn in overrides
            if fn.body is not None or fn.asm is not None
        ]
        if len(definitions) > 1:
            fn = definitions[1]
            with source_location(line=fn.line, file=fn.file):
                raise TypeError(
                    f"function '{shown_signature(fn)}' is overridden "
                    "more than once")

        if definitions:
            gen.overridden_functions.update(
                id(fn) for fn in ordinary
                if fn.body is not None or fn.asm is not None
            )
