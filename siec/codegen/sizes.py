"""Compile-time size computation for '@sizeof'."""

from dataclasses import dataclass

from llvmlite import ir

from siec.codegen.aliases import expand_alias
from siec.codegen.generator import CodeGenerator
from siec.codegen.types import (
    SCALAR_TYPES,
    fn_type_parts,
    raw_array,
    sized_array,
    strip_const,
    strip_reference,
    validate_type,
)

# each target's data layout, created once on first use
_target_data: dict = {}


def target_data(triple: str):
    """
    The ABI layout rules of a target, deciding every type's size.
    """
    if triple not in _target_data:
        from llvmlite import binding

        # any triple may be asked for, so register every backend
        binding.initialize_all_targets()
        binding.initialize_all_asmprinters()
        _target_data[triple] = (binding.Target.from_triple(triple)
                                .create_target_machine().target_data)

    return _target_data[triple]


@dataclass(frozen=True)
class TypeLayout:
    """A type's target size and natural ABI alignment, in bytes."""

    size: int
    align: int


def aligned(size: int, align: int) -> int:
    """Round a byte size up to the next multiple of an alignment."""
    return -(-size // align) * align


def primitive_layout(gen: CodeGenerator, type_: ir.Type) -> TypeLayout:
    """Ask the target ABI about one backend-independent primitive shape."""
    data = target_data(gen.target)
    return TypeLayout(
        type_.get_abi_size(data),
        type_.get_abi_alignment(data),
    )


def aggregate_layout(fields: list[TypeLayout], *,
                     packed: bool = False) -> TypeLayout:
    """Lay out a sequence of fields using the target's ordinary ABI rules."""
    if not fields:
        return TypeLayout(0, 1)
    if packed:
        return TypeLayout(sum(field.size for field in fields), 1)

    offset = 0
    alignment = max(field.align for field in fields)
    for field in fields:
        offset = aligned(offset, field.align)
        offset += field.size
    return TypeLayout(aligned(offset, alignment), alignment)


def type_layout(gen: CodeGenerator, name: str | None, *,
                active: frozenset[str] = frozenset()) -> TypeLayout:
    """
    Compute a resolved Sie type's target layout without creating output IR.

    Struct layout is derived from the semantic field inventory. Only scalar
    and pointer shapes are delegated to the target-data service, keeping
    compile-time ``@sizeof`` independent of LLVM module construction.
    """
    if name is None:
        raise TypeError("'@sizeof' needs a sized type, not void")

    validate_type(name, gen.structs)
    name = strip_const(name)

    if name.startswith("&"):
        return primitive_layout(gen, ir.PointerType(ir.IntType(8)))

    if (sized := sized_array(name)) is not None:
        name = sized[0]

    if name.startswith("closure fn("):
        pointer = primitive_layout(gen, ir.PointerType(ir.IntType(8)))
        return aggregate_layout([pointer, pointer])

    if name.startswith("fn("):
        _, _, suffix = fn_type_parts(name)
        layout = primitive_layout(gen, ir.PointerType(ir.IntType(8)))
        while suffix:
            if suffix.startswith("*"):
                layout = primitive_layout(
                    gen, ir.PointerType(ir.IntType(8)))
                suffix = suffix[1:]
            else:
                layout = aggregate_layout([
                    primitive_layout(gen, ir.PointerType(ir.IntType(8))),
                    primitive_layout(gen, SCALAR_TYPES["u64"]),
                ])
                suffix = suffix[2:]
        return layout

    stripped = name.rstrip("*")
    base, pointer_depth = stripped, len(name) - len(stripped)
    if pointer_depth:
        return primitive_layout(gen, ir.PointerType(ir.IntType(8)))

    if base.endswith("[]"):
        return aggregate_layout([
            primitive_layout(gen, ir.PointerType(ir.IntType(8))),
            primitive_layout(gen, SCALAR_TYPES["u64"]),
        ])

    if (raw := raw_array(base)) is not None:
        element = type_layout(gen, raw[0], active=active)
        return TypeLayout(element.size * int(raw[1]), element.align)

    if base in SCALAR_TYPES:
        return primitive_layout(gen, SCALAR_TYPES[base])

    info = gen.structs[base]
    if info.backing is not None:
        return type_layout(gen, info.backing, active=active)
    if info.fields is None:
        raise TypeError(f"struct {base!r} has no body and can only be "
                        f"used through a pointer ({base}*)")
    if base in active:
        raise TypeError(f"recursive struct type {base!r} needs indirection")

    fields = []
    for field in info.fields:
        if (sized := sized_array(strip_const(field.type))) is not None:
            from siec.codegen.enums import evaluate_size

            element = type_layout(
                gen, sized[0][:-2], active=active | {base})
            fields.append(TypeLayout(
                element.size * evaluate_size(gen, sized[1]),
                element.align,
            ))
        else:
            fields.append(type_layout(
                gen, field.type, active=active | {base}))
    if info.is_union:
        if not fields:
            return TypeLayout(0, 1)
        alignment = max(field.align for field in fields)
        return TypeLayout(
            aligned(max(field.size for field in fields), alignment),
            alignment,
        )
    return aggregate_layout(fields, packed=info.packed)


def size_of(gen: CodeGenerator, name: str, scope: dict | None = None) -> int:
    """
    The size in bytes of a type name, or of a variable's declared type when
    the name is one in scope (or a global); a '&T' parameter measures its T.
    """
    if scope is not None and name in scope:
        name = strip_reference(scope[name].type)
    elif (symbol := gen.resolve_symbol(name)) in gen.globals:
        name = gen.globals[symbol]

    # a measured name may be an inferred foreign type; no view gates it
    name = expand_alias(gen, name, checked=False)
    size = type_layout(gen, name).size

    # an '@align(N)' struct pads to its alignment, so arrays of it stay aligned
    if (align := gen.struct_align(name)) is not None:
        size = aligned(size, align)

    return size
