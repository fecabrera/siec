"""Registration and expansion of 'type' aliases."""

from siec.ast import Program
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator
from siec.codegen.types import SCALAR_TYPES, fn_type_parts


def register_aliases(gen: CodeGenerator, program: Program) -> None:
    """
    Register every 'type' alias, then expand each target to its canonical
    name so cycles and bad derivations surface at the declaration.
    """
    types = {s.name for s in program.structs} | {e.name for e in program.enums}

    for alias in program.aliases:
        with source_location(line=alias.line, file=alias.file):
            if alias.name in SCALAR_TYPES or alias.name == "opaque":
                raise TypeError(f"type alias {alias.name!r} shadows a builtin type")

            if alias.name in types:
                raise TypeError(f"type {alias.name!r} is declared more than once")

            if alias.name in gen.aliases:
                raise TypeError(f"type alias {alias.name!r} is declared more than once")

            gen.aliases[alias.name] = alias.type

    # expand after registration so aliases may reference one another
    # regardless of declaration order
    for alias in program.aliases:
        with source_location(line=alias.line, file=alias.file):
            gen.aliases[alias.name] = expand_alias(gen, alias.type, (alias.name,))


def expand_alias(gen: CodeGenerator, name: str | None, seen: tuple = ()) -> str | None:
    """
    Canonicalize a type name by substituting aliases with their targets,
    inside prefixes ('const', '&'), suffixes ('*', '[]', '[N]'), and
    function reference types, and settling raw array sizes to decimals.
    """
    if name is None or (not gen.aliases and "raw<" not in name):
        return name

    # prefixes wrap the expanded rest; a target's own 'const' isn't repeated
    if name.startswith("const "):
        inner = expand_alias(gen, name.removeprefix("const "), seen)
        return inner if inner.startswith("const ") else f"const {inner}"

    if name.startswith("&"):
        return f"&{expand_alias(gen, name[1:], seen)}"

    # a function reference type expands its parameter and return names,
    # keeping any '*'/'[]' suffix on the reference itself
    if name.startswith("fn("):
        params, ret, suffix = fn_type_parts(name)
        expanded = f"fn({','.join(expand_alias(gen, p, seen) for p in params)})"

        if ret is not None:
            expanded += f"->{expand_alias(gen, ret, seen)}"

        return expanded + suffix

    # a raw array expands its element and settles its size to a decimal,
    # so 'raw<byte>[N]' and 'raw<u8>[8]' agree wherever they meet
    if name.startswith("raw<"):
        # deferred import: the evaluator's module imports this one
        from siec.codegen.enums import evaluate_size
        from siec.codegen.types import raw_array

        element, size, suffix = raw_array(name)
        element = expand_alias(gen, element, seen)

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

    if base not in gen.aliases:
        return name

    if base in seen:
        cycle = " -> ".join([*seen, base])
        raise TypeError(f"type alias cycle: {cycle}")

    target = expand_alias(gen, gen.aliases[base], (*seen, base))

    # a modifier marks the whole written type; deriving a pointer or array
    # from a modified target would silently move where it applies
    if suffix and (target.startswith("const ") or target.startswith("&")):
        raise TypeError(f"cannot derive {name!r} from alias {base!r}: "
                        f"its target {target!r} carries a modifier")

    return target + suffix
