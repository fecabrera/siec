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


def is_const(name: str | None) -> bool:
    """
    Whether a type name carries the 'const' contract prefix.
    """
    return name is not None and name.startswith("const ")


def strip_const(name: str | None) -> str | None:
    """
    A type name without its 'const' contract prefix: the represented type.
    """
    return name.removeprefix("const ") if name is not None else None


def is_reference(name: str | None) -> bool:
    """
    Whether a type name is a '&T' reference, behind any 'const' marking.
    """
    return name is not None and strip_const(name).startswith("&")


def strip_reference(name: str | None) -> str | None:
    """
    The value type behind a reference: '&T' reads as T, keeping any 'const'
    marking. Non-reference names pass through unchanged.
    """
    if not is_reference(name):
        return name

    base = strip_const(name)[1:]
    return f"const {base}" if is_const(name) else base


def is_aliasing(name: str | None) -> bool:
    """
    Whether a type's values alias memory beyond their own copy: pointers
    and arrays, the types a 'const' contract must follow.
    """
    return name is not None and (name.endswith("*") or name.endswith("[]"))


def sized_array(name: str | None) -> tuple[str, str] | None:
    """
    Split a sized array name 'X[N]' into its unsized form 'X[]' and the
    size's text, or None for any other type name.

    The size is a constant integer expression's tokens, evaluated where a
    declaration allocates the backing; the type itself is just 'X[]'.
    """
    if name is None or not name.endswith("]") or name.endswith("[]"):
        return None

    base, _, size = name.rpartition("[")

    # a 'raw<T>[N]' bracket is the raw array's own size, part of its type
    if base.endswith(">"):
        return None

    return f"{base}[]", size[:-1]


def raw_array(name: str | None) -> tuple[str, str, str] | None:
    """
    Split a raw array name 'raw<T>[N]...' into its element type name, its
    size text, and any trailing suffix; None for any other type name.
    """
    if name is None or not name.startswith("raw<"):
        return None

    depth = 0
    for close, char in enumerate(name):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
            if depth == 0:
                break
    else:
        raise TypeError(f"malformed raw array type {name!r}")

    rest = name[close + 1:]
    end = rest.find("]")
    if not rest.startswith("[") or end == -1:
        raise TypeError(f"malformed raw array type {name!r}")

    return name[4:close], rest[1:end], rest[end + 1:]


NESTING = {"{": 1, "(": 1, "[": 1, "}": -1, ")": -1, "]": -1}


def anonymous_struct(name: str | None) -> tuple[bool, list, str] | None:
    """
    Split an unnamed struct or union name 'struct{a:T;b:U}...' into
    whether it's a union, its (field name, field type) pairs, and any
    trailing suffix; None for any other type name.
    """
    if name is None or not (name.startswith("struct{") or name.startswith("union{")):
        return None

    start = name.find("{")
    depth = 0
    for close in range(start, len(name)):
        depth += NESTING.get(name[close], 0)
        if depth == 0:
            break
    else:
        raise TypeError(f"malformed anonymous type {name!r}")

    body, suffix = name[start + 1:close], name[close + 1:]

    # fields split on top-level ';', each 'name:type'
    fields, depth, piece = [], 0, ""
    for char in body:
        depth += NESTING.get(char, 0)
        if char == ";" and depth == 0:
            fields.append(piece)
            piece = ""
        else:
            piece += char
    if piece:
        fields.append(piece)

    pairs = [tuple(field.split(":", 1)) for field in fields]
    return name.startswith("union{"), pairs, suffix


def type_signedness(name: str | None) -> str | None:
    """
    Classify a Sie type name as 'signed' or 'unsigned'; None for the rest.
    """
    name = strip_const(name)

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


def is_array_struct(type_: ir.Type | None) -> bool:
    """
    Whether an LLVM type has the fat array shape: a {pointer, i64} struct literal.
    """
    return (isinstance(type_, ir.LiteralStructType) and len(type_.elements) == 2
            and isinstance(type_.elements[0], ir.PointerType)
            and type_.elements[1] == ir.IntType(64))


def resolve_type(name: str | None, structs: dict | None = None,
                 allow_opaque: bool = False) -> ir.Type:
    """
    Resolve a Sie type name to an LLVM type; None resolves to void.

    Struct names resolve through the given registry, when provided. A struct
    never given a body is opaque and only resolves behind a pointer;
    allow_opaque lifts that for an array's element, held through its
    data pointer.
    """
    if name is None:
        return ir.VoidType()

    # 'const T' is a contract, not a type: it resolves as its base type
    name = strip_const(name)

    # a '&T' reference is represented by a hidden pointer to T
    if name.startswith("&"):
        return ir.PointerType(resolve_type(name[1:], structs))

    # a sized array 'X[N]' is the same fat value as 'X[]'; the size only
    # directs the automatic backing a declaration allocates
    if (sized := sized_array(name)) is not None:
        return resolve_type(sized[0], structs, allow_opaque)

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
        element = resolve_type(base[:-2], structs, allow_opaque=True)
        resolved = ir.LiteralStructType([ir.PointerType(element), ir.IntType(64)])
    elif (raw := raw_array(base)) is not None:
        # a 'raw<T>[N]' is N elements of inline storage, C's 'T[N]'
        element_name, size, _ = raw
        if not size.isdigit():
            raise TypeError(f"unresolved raw array size {size!r} in {base!r}")

        resolved = ir.ArrayType(resolve_type(element_name, structs), int(size))
    elif base == "opaque":
        if pointer_depth == 0:
            raise TypeError("'opaque' can only be used as a pointer (opaque*)")

        resolved = ir.IntType(8)  # opaque* lowers to i8*, like C's void*
    elif base in SCALAR_TYPES:
        resolved = SCALAR_TYPES[base]
    elif structs and base in structs:
        if (structs[base].fields is None and pointer_depth == 0 and not allow_opaque):
            raise TypeError(f"struct {base!r} has no body and can only be "
                            f"used through a pointer ({base}*)")

        resolved = structs[base].type
    else:
        raise TypeError(f"unknown type {base!r}")

    # wrap the base type in one pointer per '*'
    for _ in range(pointer_depth):
        resolved = ir.PointerType(resolved)
    
    return resolved
