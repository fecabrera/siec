"""Monomorphization of generic structs.

A 'struct S<T>' declaration registers a template; each concrete spelling
'S<i32>' met in a type position stamps out a real struct under that
canonical name, so every use of the same arguments shares one type.
"""

import re

from siec.ast import Field
from siec.codegen.generator import CodeGenerator, StructInfo
from siec.codegen.types import is_reference, resolve_type

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
INSTANTIATION = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)<")


def check_template_cycle(gen: CodeGenerator, name: str) -> None:
    """
    Reject a generic alias whose target reaches back to itself through
    other generic aliases: 'A<T> = B<T>; B<T> = A<T>' can only loop.

    Plain aliases expand eagerly at declaration, so only the edges among
    templates need walking here; mixed cycles surface through that
    eager expansion.
    """
    def references(base: str):
        target = gen.generic_aliases[base].type
        return [m.group(1) for m in INSTANTIATION.finditer(target)
                if m.group(1) in gen.generic_aliases]

    def visit(base: str, path: tuple) -> None:
        if base in path:
            cycle = [*path[path.index(base):], base]
            raise TypeError("type alias cycle: " + " -> ".join(cycle))

        for ref in references(base):
            visit(ref, (*path, base))

    visit(name, ())


def split_generic(name: str) -> tuple[str, list[str]] | None:
    """
    Split a generic spelling 'Name<A,B>' into its base name and argument
    names, or None for any other shape.

    Arguments split on top-level commas only: brackets of any kind nest,
    and the '>' of a function type's '->' closes nothing.
    """
    base, sep, rest = name.partition("<")
    if not sep or not base.isidentifier() or not name.endswith(">"):
        return None

    inner = rest[:-1]
    args, depth, start = [], 0, 0
    for i, char in enumerate(inner):
        if char in "<([{":
            depth += 1
        elif char == ">" and inner[i - 1:i] == "-":
            continue
        elif char in ">)]}":
            depth -= 1
        elif char == "," and depth == 0:
            args.append(inner[start:i])
            start = i + 1

    if depth != 0:
        return None

    return base, [*args, inner[start:]]


def substitute(type_name: str, mapping: dict) -> str:
    """
    Replace each type parameter's whole-identifier occurrences in a field's
    type name: 'T*' becomes 'i32*', 'Tx' stays itself.
    """
    return IDENT.sub(lambda m: mapping.get(m.group(), m.group()), type_name)


def instantiate_generic(gen: CodeGenerator, name: str, seen: tuple = ()) -> str | None:
    """
    Instantiate a generic spelling into a concrete canonical name: a
    struct template registers a real struct, an alias template expands
    its substituted target; None when the base is not a known template.

    A struct's identified type registers before its fields resolve, so a
    field may point at the instantiation itself, or at a mutually
    generic one.
    """
    # deferred imports: instantiation is a stage of alias expansion
    from siec.codegen.aliases import expand_alias
    from siec.codegen.structs import union_storage

    if (parts := split_generic(name)) is None:
        return None

    base, args = parts
    alias = gen.generic_aliases.get(base)
    template = gen.generic_structs.get(base)
    if alias is None and template is None:
        return None

    params = alias.params if alias is not None else template.params
    kind = "type alias" if alias is not None else "struct"

    args = [expand_alias(gen, arg, seen) for arg in args]
    if len(args) != len(params):
        take = len(params)
        raise TypeError(f"generic {kind} {base!r} takes {take} type "
                        f"argument{'s' if take != 1 else ''}, got {len(args)}")

    # a modifier marks a whole written type; substituted into a derived
    # position ('T*'), it would silently move where it applies
    for arg in args:
        if arg.startswith("const ") or arg.startswith("&"):
            raise TypeError(f"cannot instantiate {base!r} with {arg!r}: "
                            "the argument carries a modifier")

    # a generic alias expands its target with the arguments substituted,
    # like any alias one step further; 'seen' catches self-reference
    if alias is not None:
        if base in seen:
            cycle = " -> ".join([*seen, base])
            raise TypeError(f"type alias cycle: {cycle}")

        target = substitute(alias.type, dict(zip(params, args)))
        return expand_alias(gen, target, (*seen, base))

    canonical = f"{base}<{','.join(args)}>"
    if canonical in gen.structs:
        return canonical

    if template.fields is None:
        raise TypeError(f"generic struct {base!r} is declared without a body")

    ident = gen.module.context.get_identified_type(canonical)
    mapping = dict(zip(template.params, args))
    fields = [Field(f.name, substitute(f.type, mapping)) for f in template.fields]

    info = StructInfo(ident, fields, align=template.align,
                      volatile=template.volatile, is_union=template.is_union)
    if template.packed:
        ident.packed = True

    gen.structs[canonical] = info

    for field in fields:
        field.type = expand_alias(gen, field.type, seen)
        if is_reference(field.type):
            raise TypeError(f"field {field.name!r} cannot be a reference")

    resolved = [resolve_type(f.type, gen.structs) for f in fields]
    if info.is_union:
        resolved = union_storage(gen, resolved)

    ident.set_body(*resolved)
    return canonical
