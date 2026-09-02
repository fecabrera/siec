"""Resolution of Sie type names to LLVM types."""

from llvmlite import ir

from siec.codegen.type_refs import TypeRef, derivation, parse_type_ref

SCALAR_TYPES = {
    "i8": ir.IntType(8),
    "i16": ir.IntType(16),
    "i32": ir.IntType(32),
    "i64": ir.IntType(64),
    "i128": ir.IntType(128),
    "u8": ir.IntType(8),
    "u16": ir.IntType(16),
    "u32": ir.IntType(32),
    "u64": ir.IntType(64),
    "u128": ir.IntType(128),
    "f32": ir.FloatType(),
    "f64": ir.DoubleType(),
    "bool": ir.IntType(1),
    "char": ir.IntType(8),
}


SIGNED_TYPES = {"i8", "i16", "i32", "i64", "i128"}
UNSIGNED_TYPES = {"u8", "u16", "u32", "u64", "u128"}
INTEGER_TYPES = SIGNED_TYPES | UNSIGNED_TYPES


def is_const(name: str | None) -> bool:
    """
    Whether a type name carries the 'const' contract prefix.
    """
    return bool(name) and parse_type_ref(name).kind == "const"


def strip_const(name: str | None) -> str | None:
    """
    A type name without its 'const' contract prefix: the represented type.
    """
    if name is None:
        return None
    if not name:
        return name
    ref = parse_type_ref(name)
    return ref.inner.spelling() if ref.kind == "const" else name


def is_nonnull_pointer(name: str | None) -> bool:
    """Whether a type carries the non-null pointer contract."""
    if not name:
        return False
    ref = parse_type_ref(name)
    if ref.kind == "const":
        ref = ref.inner
    return ref.kind == "nonnull"


def strip_nonnull(name: str | None) -> str | None:
    """Remove a non-null pointer contract while preserving outer const."""
    if not is_nonnull_pointer(name):
        return name

    ref = parse_type_ref(name)
    if ref.kind == "const":
        return TypeRef("const", inner=ref.inner.inner).spelling()
    return ref.inner.spelling()


def nonnull_pointer(name: str) -> str:
    """Add a non-null contract to one pointer type."""
    ref = parse_type_ref(name)
    if ref.kind == "const":
        inner = ref.inner
        if inner.kind != "nonnull":
            inner = TypeRef("nonnull", inner=inner)
        return TypeRef("const", inner=inner).spelling()
    return (ref if ref.kind == "nonnull"
            else TypeRef("nonnull", inner=ref)).spelling()


def is_reference(name: str | None) -> bool:
    """
    Whether a type name is a '&T' reference, behind any 'const' marking.
    """
    if not name:
        return False
    ref = parse_type_ref(name)
    if ref.kind == "const":
        ref = ref.inner
    return ref.kind == "reference"


def strip_reference(name: str | None) -> str | None:
    """
    The value type behind a reference: '&T' reads as T, keeping any 'const'
    marking. Non-reference names pass through unchanged.
    """
    if not is_reference(name):
        return name

    ref = parse_type_ref(name)
    if ref.kind == "const":
        return TypeRef("const", inner=ref.inner.inner).spelling()
    return ref.inner.spelling()


def is_aliasing(name: str | None) -> bool:
    """
    Whether a type's values alias memory beyond their own copy: pointers
    and arrays, the types a 'const' contract must follow.
    """
    if not name:
        return False
    ref = parse_type_ref(name)
    while ref.kind in ("const", "reference", "nonnull"):
        ref = ref.inner
    return ref.kind in ("pointer", "array")


def sized_array(name: str | None) -> tuple[str, str] | None:
    """
    Split a sized array name 'X[N]' into its unsized form 'X[]' and the
    size's text, or None for any other type name.

    The size is a constant integer expression's tokens, evaluated where a
    declaration allocates the backing; the type itself is just 'X[]'.
    """
    if not name:
        return None
    ref = parse_type_ref(name)
    if ref.kind != "sized":
        return None
    return TypeRef("array", inner=ref.inner).spelling(), ref.size


def raw_array(name: str | None) -> tuple[str, str, str] | None:
    """
    Split a raw array name 'raw<T>[N]...' into its element type name, its
    size text, and any trailing suffix; None for any other type name.
    """
    if not name:
        return None
    base, suffix = derivation(parse_type_ref(name))
    if base.kind != "raw":
        return None
    return base.inner.spelling(), base.size, suffix


def anonymous_struct(name: str | None) -> tuple[bool, list, str] | None:
    """
    Split an unnamed struct or union name 'struct{a:T;b:U}...' into
    whether it's a union, its (field name, field type) pairs, and any
    trailing suffix; None for any other type name.
    """
    if not name:
        return None
    base, suffix = derivation(parse_type_ref(name))
    if base.kind not in ("struct", "union"):
        return None
    pairs = [(field, type_.spelling()) for field, type_ in base.fields]
    return base.kind == "union", pairs, suffix


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
    base, suffix = derivation(parse_type_ref(name))
    if base.kind not in ("function", "closure"):
        raise TypeError(f"malformed function type {name!r}")
    params = [param.spelling() for param in base.items]
    result = base.result.spelling() if base.result is not None else None
    return params, result, suffix


def is_array_struct(type_: ir.Type | None) -> bool:
    """
    Whether an LLVM type has the fat array shape: a {pointer, i64} struct literal.
    """
    return (isinstance(type_, ir.LiteralStructType) and len(type_.elements) == 2
            and isinstance(type_.elements[0], ir.PointerType)
            and type_.elements[1] == ir.IntType(64))


def validate_type(name: str | None, structs: dict | None = None,
                  allow_opaque: bool = False) -> None:
    """
    Validate a resolved Sie type spelling without constructing an LLVM type.

    This is the semantic counterpart of ``resolve_type``. Collection and
    resolution use it while the backend is still empty; LLVM lowering calls
    ``resolve_type`` only after the checked type inventory has been frozen.
    """
    if name is None:
        return
    _validate_ref(parse_type_ref(name), structs, allow_opaque)


def _validate_ref(ref: TypeRef | None, structs: dict | None,
                  allow_opaque: bool = False) -> None:
    """Validate one structural type reference."""
    if ref is None:
        return
    if ref.kind == "const":
        _validate_ref(ref.inner, structs, allow_opaque)
        return
    if ref.kind == "nonnull":
        if ref.inner.kind != "pointer":
            raise TypeError("'!' can only qualify a pointer type")
        _validate_ref(ref.inner, structs, allow_opaque)
        return
    if ref.kind == "reference":
        _validate_ref(ref.inner, structs)
        return
    if ref.kind == "pointer":
        if ref.inner.kind == "closure":
            raise TypeError(f"malformed closure type {ref.spelling()!r}")
        _validate_ref(ref.inner, structs, allow_opaque=True)
        return
    if ref.kind in ("array", "sized"):
        _validate_ref(ref.inner, structs, allow_opaque=True)
        return
    if ref.kind == "raw":
        if not ref.size.isdigit():
            raise TypeError(
                f"unresolved raw array size {ref.size!r} in {ref.spelling()!r}")
        _validate_ref(ref.inner, structs)
        return
    if ref.kind == "closure":
        _validate_ref(ref.result, structs)
        for param in ref.items:
            _validate_ref(param, structs)
        return
    if ref.kind == "function":
        _validate_ref(ref.result, structs)
        for param in ref.items:
            _validate_ref(param, structs)
        return

    name = ref.spelling()
    if name == "opaque":
        if not allow_opaque:
            raise TypeError("'opaque' can only be used as a pointer (opaque*)")
    elif name in SCALAR_TYPES:
        return
    elif structs and name in structs:
        if structs[name].fields is None and not allow_opaque:
            raise TypeError(f"struct {name!r} has no body and can only be "
                            f"used through a pointer ({name}*)")
    else:
        raise TypeError(f"unknown type {name!r}")


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
    ref = parse_type_ref(name)
    _validate_ref(ref, structs, allow_opaque)
    return _resolve_ref(ref, structs, allow_opaque)


def _resolve_ref(ref: TypeRef, structs: dict | None,
                 allow_opaque: bool = False) -> ir.Type:
    """Lower one validated structural type reference to LLVM."""
    if ref.kind in ("const", "nonnull"):
        return _resolve_ref(ref.inner, structs, allow_opaque)
    if ref.kind == "reference":
        return ir.PointerType(_resolve_ref(ref.inner, structs))
    if ref.kind == "pointer":
        return ir.PointerType(_resolve_ref(ref.inner, structs, True))
    if ref.kind in ("array", "sized"):
        element = _resolve_ref(ref.inner, structs, True)
        return ir.LiteralStructType([ir.PointerType(element), ir.IntType(64)])
    if ref.kind == "raw":
        return ir.ArrayType(
            _resolve_ref(ref.inner, structs), int(ref.size))
    if ref.kind == "closure":
        opaque = ir.PointerType(ir.IntType(8))
        return ir.LiteralStructType([opaque, opaque])
    if ref.kind == "function":
        return ir.PointerType(ir.FunctionType(
            (_resolve_ref(ref.result, structs)
             if ref.result is not None else ir.VoidType()),
            [_resolve_ref(param, structs) for param in ref.items],
        ))

    name = ref.spelling()
    if name == "opaque":
        return ir.IntType(8)
    if name in SCALAR_TYPES:
        return SCALAR_TYPES[name]
    return structs[name].type
