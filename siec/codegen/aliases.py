"""Registration and expansion of 'type' aliases."""

import re

from siec.ast import Program
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator
from siec.codegen.types import SCALAR_TYPES, fn_type_parts

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def register_anonymous(gen: CodeGenerator, name: str, is_union: bool,
                       pairs: list) -> None:
    """
    Register an unnamed struct or union under its canonical name, once,
    so member access and layout treat it like any declared type.

    A field naming a struct not yet registered leaves it for a later
    use to register, when the registry has filled in.
    """
    if name in gen.structs:
        return

    # deferred imports: struct registration builds on this module
    from siec.ast import Field
    from siec.codegen.generator import StructInfo
    from siec.codegen.types import validate_type

    fields = [Field(field, type_) for field, type_ in pairs]

    try:
        for field in fields:
            validate_type(field.type, gen.structs)
    except TypeError:
        return

    if gen.types_lowered:
        raise RuntimeError(
            "LLVM emission attempted to register an anonymous type")

    gen.structs[name] = StructInfo(
        None,
        fields,
        is_union=is_union,
        literal=True,
    )


def names_type(gen: CodeGenerator, name: str) -> bool:
    """
    Whether a name is a declared type identity, wherever it was declared.
    """
    return (name in gen.structs or name in gen.enums or name in gen.aliases
            or name in gen.alias_targets
            or name in gen.generic_structs or name in gen.generic_aliases
            or name in gen.interfaces)


def type_identity(gen: CodeGenerator, name: str) -> str | None:
    """
    The declaration category currently owning a type name.

    Enums also carry an entry in 'structs' for their backing representation,
    so the more specific registries take precedence. Keeping this arbitration
    in one place makes every collector enforce the same type namespace.
    """
    if name in SCALAR_TYPES or name in ("opaque", "Tuple"):
        return "builtin"

    if (name in gen.aliases or name in gen.alias_targets
            or name in gen.generic_aliases):
        return "alias"

    if name in gen.enums:
        return "enum"

    if name in gen.interfaces:
        return "interface"

    if name in gen.generic_structs:
        return "generic struct"

    if name in gen.structs:
        return "struct"

    return None


def collect_aliases(gen: CodeGenerator, program: Program) -> None:
    """
    Add raw alias declarations to the type-identity inventory.

    Collection records names and syntax only. Target expansion, generic
    template cycles, and invalid derivations wait for ``resolve_aliases``.
    """
    if gen.declaration_inventory_complete:
        raise RuntimeError(
            "alias collection continued after its inventory was frozen")

    types = {s.name for s in program.structs} | {e.name for e in program.enums}

    for alias in program.aliases:
        declaration_id = id(alias)
        if declaration_id in gen.collected_aliases:
            continue

        with source_location(line=alias.line, file=alias.file):
            gen.current_file = alias.file

            if alias.name in SCALAR_TYPES or alias.name in ("opaque", "Tuple"):
                raise TypeError(f"type alias {alias.name!r} shadows a builtin type")

            if alias.name in types:
                raise TypeError(f"type {alias.name!r} is declared more than once")

            owner = type_identity(gen, alias.name)
            if owner == "alias":
                raise TypeError(f"type alias {alias.name!r} is declared more than once")

            if owner is not None:
                raise TypeError(f"type {alias.name!r} is declared more than once")

            # a generic alias is a template, expanded when a concrete
            # 'a<args>' spelling supplies its arguments
            if alias.params is not None:
                gen.generic_aliases[alias.name] = alias
            else:
                gen.alias_targets[alias.name] = alias.type

            gen.collected_aliases.add(declaration_id)
            gen.alias_declarations.append(alias)


def resolve_aliases(gen: CodeGenerator) -> None:
    """Resolve every collected alias target and generic template cycle."""
    for alias in gen.alias_declarations:
        identity = id(alias)
        if identity in gen.resolved_aliases:
            continue

        with source_location(line=alias.line, file=alias.file):
            gen.current_file = alias.file

            if alias.params is None:
                gen.aliases[alias.name] = expand_alias(gen, alias.type, (alias.name,))
            else:
                from siec.codegen.generics import check_template_cycle

                check_template_cycle(gen, alias.name)

            gen.resolved_aliases.add(identity)


def expand_alias(gen: CodeGenerator, name: str | None, seen: tuple = (),
                 checked: bool = True,
                 parameters=frozenset()) -> str | None:
    """
    Canonicalize a type name by substituting aliases with their targets,
    inside prefixes ('const', '&'), suffixes ('*', '[]', '[N]'), and
    function reference types, and settling raw array sizes to decimals.

    A checked expansion holds written names to the file's view: a dotted
    name resolves through its module binding, and an unqualified one must
    be visible here. Names the compiler carries itself - inferred types,
    substituted generics - expand unchecked. Lexical 'parameters' remain
    placeholders even when a global type has the same name.
    """
    if name is None:
        return None

    if (not gen.aliases and not gen.alias_targets
            and not gen.visible and not gen.interfaces
            and not any(m in name for m in ("<", "struct{", "union{", "."))):
        return name

    checked = checked and not gen.ungated_types

    # prefixes wrap the expanded rest; a target's own 'const' isn't repeated
    if name.startswith("const "):
        inner = expand_alias(
            gen, name.removeprefix("const "), seen, checked, parameters)
        return inner if inner.startswith("const ") else f"const {inner}"

    if name.startswith("&"):
        return f"&{expand_alias(gen, name[1:], seen, checked, parameters)}"

    # a function reference type expands its parameter and return names,
    # keeping any '*'/'[]' suffix on the reference itself
    if name.startswith("fn(") or name.startswith("closure fn("):
        closure = name.startswith("closure ")
        params, ret, suffix = fn_type_parts(name)
        expanded_params = ",".join(
            expand_alias(gen, p, seen, checked, parameters)
            for p in params
        )
        expanded = f"{'closure ' if closure else ''}fn({expanded_params})"

        if ret is not None:
            expanded += f"->{expand_alias(
                gen, ret, seen, checked, parameters)}"

        return expanded + suffix

    # an unnamed struct or union expands its field types and registers
    # under its canonical name, so identical shapes are one type
    if name.startswith("struct{") or name.startswith("union{"):
        from siec.codegen.types import anonymous_struct

        is_union, pairs, suffix = anonymous_struct(name)
        pairs = [(field, expand_alias(
            gen, type_, seen, checked, parameters))
                 for field, type_ in pairs]

        kind = "union" if is_union else "struct"
        canon = kind + "{" + ";".join(f"{f}:{t}" for f, t in pairs) + "}"
        register_anonymous(gen, canon, is_union, pairs)
        return canon + suffix

    # a raw array expands its element and settles its size to a decimal,
    # so 'raw<byte>[N]' and 'raw<u8>[8]' agree wherever they meet
    if name.startswith("raw<"):
        # deferred import: the evaluator's module imports this one
        from siec.codegen.enums import evaluate_size
        from siec.codegen.types import raw_array

        element, size, suffix = raw_array(name)
        element = expand_alias(gen, element, seen, checked, parameters)

        if not size.isdigit():
            size = str(evaluate_size(gen, size))

        return f"raw<{element}>[{size}]{suffix}"

    # peel derivation suffixes down to the base name; sizes pass through
    # untouched for codegen to evaluate
    base, suffix = name, ""
    while True:
        if base.endswith("*"):
            base, suffix = base[:-1], f"*{suffix}"
        elif base.endswith("]"):
            head, _, size = base.rpartition("[")
            base, suffix = head, f"[{size}{suffix}"
        else:
            break

    # a dotted base reaches a type through the file's module bindings,
    # its membership validated against the module's exports
    if "." in base:
        head, angle, rest = base.partition("<")
        member = gen.resolve_qualified(head.split("."))
        if member is None:
            raise TypeError(f"unknown type {name!r}")

        return expand_alias(
            gen,
            member + angle + rest,
            seen,
            checked=False,
            parameters=parameters,
        ) + suffix

    # a member import binds a module's type under the file's chosen name;
    # a generic spelling translates its base, keeping the arguments
    head, angle, rest = base.partition("<")
    if head in parameters:
        return base + suffix

    if checked and (bound := gen.member_bindings.get((gen.current_file, head))):
        if bound != head and names_type(gen, bound):
            return expand_alias(
                gen,
                bound + angle + rest,
                seen,
                checked=False,
                parameters=parameters,
            ) + suffix

    # A lexical parameter owns each of its occurrences, including inside a
    # derived or generic spelling. Keep the template shape intact until its
    # concrete substitution resolves it.
    if (parameters
            and any(match.group() in parameters
                    for match in IDENT.finditer(base))):
        return base + suffix

    # a 'Name<args>' base instantiates a generic struct or expands a
    # generic alias, landing on the concrete canonical spelling
    if "<" in base:
        from siec.codegen.generics import instantiate_generic

        if (generic := instantiate_generic(gen, base, seen, checked)) is not None:
            if suffix and (generic.startswith("const ") or generic.startswith("&")):
                raise TypeError(f"cannot derive {name!r} from {base!r}: its "
                                f"target {generic!r} carries a modifier")

            return generic + suffix

    if base not in gen.aliases and base not in gen.alias_targets:
        # an interface is abstract: only a parameter's type can take one,
        # standing for any implementing struct
        if base.partition("<")[0] in gen.interfaces:
            named = base.partition("<")[0]
            raise TypeError(f"interface {named!r} is not a concrete type: "
                            "only a parameter can take an interface, "
                            "standing for any struct implementing it")

        # a type declared by an unimported module doesn't resolve here
        if (checked and not gen.sees(base)
                and (base in gen.structs or base in gen.enums
                     or base in gen.generic_structs)):
            raise TypeError(f"unknown type {base!r}")

        return name

    if checked and not gen.sees(base):
        raise TypeError(f"unknown type {base!r}")

    if base in seen:
        cycle = " -> ".join([*seen, base])
        raise TypeError(f"type alias cycle: {cycle}")

    target = expand_alias(
        gen,
        gen.aliases.get(base, gen.alias_targets.get(base)),
        (*seen, base),
        checked=False,
        parameters=parameters,
    )

    # a modifier marks the whole written type; deriving a pointer or array
    # from a modified target would silently move where it applies
    if suffix and (target.startswith("const ") or target.startswith("&")):
        raise TypeError(f"cannot derive {name!r} from alias {base!r}: "
                        f"its target {target!r} carries a modifier")

    return target + suffix
