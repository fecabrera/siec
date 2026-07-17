"""Resolution of Sie type names to LLVM types."""

from llvmlite import ir

SCALAR_TYPES = {
    "i8": ir.IntType(8),
    "i16": ir.IntType(16),
    "i32": ir.IntType(32),
    "i64": ir.IntType(64),
    "u8": ir.IntType(8),
    "u16": ir.IntType(16),
    "u32": ir.IntType(32),
    "u64": ir.IntType(64),
    "f32": ir.FloatType(),
    "f64": ir.DoubleType(),
    "bool": ir.IntType(1),
    "char": ir.IntType(8),
}


SIGNED_TYPES = {"i8", "i16", "i32", "i64"}
UNSIGNED_TYPES = {"u8", "u16", "u32", "u64"}


def type_signedness(name: str | None) -> str | None:
    """
    Classify a Sie type name as 'signed' or 'unsigned'; None for the rest.
    """
    if name in SIGNED_TYPES:
        return "signed"

    if name in UNSIGNED_TYPES:
        return "unsigned"

    return None


def fn_type_parts(name: str) -> tuple[list[str], str | None, str]:
    """
    Split a canonical 'fn(...)' type name into its parameter type names, return
    type name (None for void), and any trailing suffix ('*' or '[]' forms).

    A '->' after the parameter list claims the whole rest of the name for the
    return type; a suffix can only follow a function type with no return.
    """
    # find the ')' matching the opening paren of the parameter list
    depth = 0
    for end, ch in enumerate(name):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
    else:
        raise TypeError(f"malformed function type {name!r}")

    inner, rest = name[3:end], name[end + 1:]

    ret = None
    if rest.startswith("->"):
        ret, rest = rest[2:], ""

    # split the parameter list on top-level commas, leaving nested fn types whole
    params, depth, start = [], 0, 0
    for i, ch in enumerate(inner):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            params.append(inner[start:i])
            start = i + 1
    if inner:
        params.append(inner[start:])

    return params, ret, rest


def resolve_type(name: str | None, structs: dict | None = None) -> ir.Type:
    """
    Resolve a Sie type name to an LLVM type; None resolves to void.

    Struct names resolve through the given registry, when provided.
    """
    if name is None:
        return ir.VoidType()

    # a function reference type resolves to a pointer to the function's signature
    if name.startswith("fn("):
        params, ret, suffix = fn_type_parts(name)
        resolved = ir.PointerType(ir.FunctionType(
            resolve_type(ret, structs),
            [resolve_type(p, structs) for p in params]))

        # a suffix wraps the reference itself: '*' in a pointer, '[]' in an array
        while suffix:
            if suffix.startswith("*"):
                resolved = ir.PointerType(resolved)
                suffix = suffix[1:]
            elif suffix.startswith("[]"):
                resolved = ir.LiteralStructType([ir.PointerType(resolved), ir.IntType(64)])
                suffix = suffix[2:]
            else:
                raise TypeError(f"malformed function type {name!r}")

        return resolved

    # peel trailing '*'s into a pointer depth; a '*' inside the name
    # (an array of pointers, say) stays part of the base
    stripped = name.rstrip("*")
    base, pointer_depth = stripped, len(name) - len(stripped)

    if base.endswith("[]"):
        # an 'X[]' array is a struct of a pointer to X and a u64 length
        element = resolve_type(base[:-2], structs)
        resolved = ir.LiteralStructType([ir.PointerType(element), ir.IntType(64)])
    elif base == "opaque":
        if pointer_depth == 0:
            raise TypeError("'opaque' can only be used as a pointer (opaque*)")

        resolved = ir.IntType(8)  # opaque* lowers to i8*, like C's void*
    elif base in SCALAR_TYPES:
        resolved = SCALAR_TYPES[base]
    elif structs and base in structs:
        resolved = structs[base].type
    else:
        raise TypeError(f"unknown type {base!r}")

    # wrap the base type in one pointer per '*'
    for _ in range(pointer_depth):
        resolved = ir.PointerType(resolved)
    
    return resolved
