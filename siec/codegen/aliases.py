"""Registration and expansion of 'type' aliases."""

from siec.ast import Program
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator
from siec.codegen.types import SCALAR_TYPES, fn_type_parts


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
    from llvmlite import ir

    from siec.ast import Field
    from siec.codegen.generator import StructInfo
    from siec.codegen.structs import union_storage
    from siec.codegen.types import resolve_type

    fields = [Field(field, type_) for field, type_ in pairs]

    try:
        resolved = [resolve_type(f.type, gen.structs) for f in fields]
    except TypeError:
        return

    if is_union:
        resolved = union_storage(gen, resolved)

    gen.structs[name] = StructInfo(ir.LiteralStructType(resolved), fields,
                                   is_union=is_union)


def names_type(gen: CodeGenerator, name: str) -> bool:
    """
    Whether a name is a declared type's: a struct, enum, alias, or
    generic template, wherever it was declared.
    """
    return (name in gen.structs or name in gen.enums or name in gen.aliases
            or name in gen.generic_structs or name in gen.generic_aliases)


def register_aliases(gen: CodeGenerator, program: Program) -> None:
    """
    Register every 'type' alias, then expand each target to its canonical
    name so cycles and bad derivations surface at the declaration.
    """
    types = {s.name for s in program.structs} | {e.name for e in program.enums}

    for alias in program.aliases:
        with source_location(line=alias.line, file=alias.file):
            gen.current_file = alias.file

            if alias.name in SCALAR_TYPES or alias.name == "opaque":
                raise TypeError(f"type alias {alias.name!r} shadows a builtin type")

            if alias.name in types:
                raise TypeError(f"type {alias.name!r} is declared more than once")

            if alias.name in gen.aliases or alias.name in gen.generic_aliases:
                raise TypeError(f"type alias {alias.name!r} is declared more than once")

            # a generic alias is a template, expanded when a concrete
            # 'a<args>' spelling supplies its arguments
            if alias.params is not None:
                gen.generic_aliases[alias.name] = alias
            else:
                gen.aliases[alias.name] = alias.type

    # expand after registration so aliases may reference one another
    # regardless of declaration order; a generic template cannot expand
    # without arguments, but a cycle among templates is checkable now
    for alias in program.aliases:
        with source_location(line=alias.line, file=alias.file):
            gen.current_file = alias.file

            if alias.params is None:
                gen.aliases[alias.name] = expand_alias(gen, alias.type, (alias.name,))
            else:
                from siec.codegen.generics import check_template_cycle

                check_template_cycle(gen, alias.name)


def expand_alias(gen: CodeGenerator, name: str | None, seen: tuple = (),
                 checked: bool = True) -> str | None:
    """
    Canonicalize a type name by substituting aliases with their targets,
    inside prefixes ('const', '&'), suffixes ('*', '[]', '[N]'), and
    function reference types, and settling raw array sizes to decimals.

    A checked expansion holds written names to the file's view: a dotted
    name resolves through its module binding, and an unqualified one must
    be visible here. Names the compiler carries itself - inferred types,
    substituted generics - expand unchecked.
    """
    if name is None:
        return None

    if (not gen.aliases and not gen.visible
            and not any(m in name for m in ("<", "struct{", "union{", "."))):
        return name

    checked = checked and not gen.ungated_types

    # prefixes wrap the expanded rest; a target's own 'const' isn't repeated
    if name.startswith("const "):
        inner = expand_alias(gen, name.removeprefix("const "), seen, checked)
        return inner if inner.startswith("const ") else f"const {inner}"

    if name.startswith("&"):
        return f"&{expand_alias(gen, name[1:], seen, checked)}"

    # a function reference type expands its parameter and return names,
    # keeping any '*'/'[]' suffix on the reference itself
    if name.startswith("fn("):
        params, ret, suffix = fn_type_parts(name)
        expanded = f"fn({','.join(expand_alias(gen, p, seen, checked) for p in params)})"

        if ret is not None:
            expanded += f"->{expand_alias(gen, ret, seen, checked)}"

        return expanded + suffix

    # an unnamed struct or union expands its field types and registers
    # under its canonical name, so identical shapes are one type
    if name.startswith("struct{") or name.startswith("union{"):
        from siec.codegen.types import anonymous_struct

        is_union, pairs, suffix = anonymous_struct(name)
        pairs = [(field, expand_alias(gen, type_, seen, checked))
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
        element = expand_alias(gen, element, seen, checked)

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

        return expand_alias(gen, member + angle + rest, seen, checked=False) + suffix

    # a member import binds a module's type under the file's chosen name;
    # a generic spelling translates its base, keeping the arguments
    head, angle, rest = base.partition("<")
    if checked and (bound := gen.member_bindings.get((gen.current_file, head))):
        if bound != head and names_type(gen, bound):
            return expand_alias(gen, bound + angle + rest, seen,
                                checked=False) + suffix

    # a 'Name<args>' base instantiates a generic struct or expands a
    # generic alias, landing on the concrete canonical spelling
    if "<" in base:
        from siec.codegen.generics import instantiate_generic

        if (generic := instantiate_generic(gen, base, seen, checked)) is not None:
            if suffix and (generic.startswith("const ") or generic.startswith("&")):
                raise TypeError(f"cannot derive {name!r} from {base!r}: its "
                                f"target {generic!r} carries a modifier")

            return generic + suffix

    if base not in gen.aliases:
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

    target = expand_alias(gen, gen.aliases[base], (*seen, base), checked=False)

    # a modifier marks the whole written type; deriving a pointer or array
    # from a modified target would silently move where it applies
    if suffix and (target.startswith("const ") or target.startswith("&")):
        raise TypeError(f"cannot derive {name!r} from alias {base!r}: "
                        f"its target {target!r} carries a modifier")

    return target + suffix
