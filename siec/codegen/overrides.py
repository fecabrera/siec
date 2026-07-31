"""Validation and selection of concrete ``@override`` declarations."""

from siec.codegen.errors import source_location
from siec.codegen.generics import split_generic, unify
from siec.codegen.interfaces import constraints_hold
from siec.codegen.methods import method_signature
from siec.codegen.overloads import overload_symbol, shown_signature
from siec.codegen.types import strip_const


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


def canonical_method_signature(gen, fn, mapping: dict | None = None) -> tuple:
    """A method signature with aliases expanded in its declaration's view."""
    from siec.codegen.aliases import expand_alias

    params, ret = method_signature(fn, mapping)
    previous, gen.current_file = gen.current_file, fn.file
    try:
        # Receiver-family signatures still contain source spellings and
        # resolve through their declaring file. Concrete methods have already
        # been registered and canonicalized; their carried names may refer to
        # an alias target that was not itself imported into that file. A
        # substituted signature likewise carries the overriding site's
        # concrete arguments, which the template's file need not see:
        # compiler-carried names expand unchecked.
        checked = fn.receiver_params is not None and not mapping
        params = tuple(
            strip_const(expand_alias(gen, param, checked=checked))
            for param in params
        )
        ret = (strip_const(expand_alias(gen, ret, checked=checked))
               if ret is not None else None)
        return params, ret
    finally:
        gen.current_file = previous


def family_method_targets(gen, fn) -> set[tuple]:
    """
    The written signatures of ordinary receiver families a method overrides.

    ``char[]::f`` can therefore override ``T[]::f`` without first declaring
    a redundant concrete forwarding signature. Matching uses canonical types,
    while the written signatures are retained so method stamping can suppress
    precisely those family entries later.
    """
    if fn.receiver is None:
        return set()

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

    wanted = canonical_method_signature(gen, fn)
    return {
        method_signature(template, mapping)
        for template, mapping in entries
        if (not template.is_override
            and constraints_hold(
                gen, template.receiver_constraints, mapping, template.file)
            and canonical_method_signature(gen, template, mapping) == wanted)
    }


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
                family_targets = family_method_targets(gen, fn)
                if not ordinary and not family_targets:
                    raise TypeError(
                        f"function '{shown_signature(fn)}' has no matching "
                        "declaration to override")
                if fn.receiver is not None:
                    gen.overridden_method_signatures.setdefault(
                        fn.name, set()).update(
                            family_targets or {method_signature(fn)})

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
