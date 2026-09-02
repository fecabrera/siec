"""Shared construction of array values and their non-null empty backing."""

from llvmlite import ir


def empty_array_data(gen, element_type: ir.Type) -> ir.Constant:
    """Return a stable non-null pointer for an empty array element type."""
    key = str(element_type)
    backing = gen.array_sentinels.get(key)
    if backing is None:
        backing_type = ir.ArrayType(element_type, 1)
        backing = ir.GlobalVariable(
            gen.module,
            backing_type,
            name=f".siec.empty.array.{len(gen.array_sentinels)}",
        )
        backing.linkage = "internal"
        backing.global_constant = True
        backing.initializer = ir.Constant(backing_type, None)
        gen.array_sentinels[key] = backing

    zero = ir.Constant(ir.IntType(32), 0)
    return backing.gep([zero, zero])


def empty_array_value(gen, array_type: ir.Type) -> ir.Constant:
    """Return an empty array descriptor whose data pointer is non-null."""
    return ir.Constant(array_type, [
        empty_array_data(gen, array_type.elements[0].pointee),
        ir.Constant(ir.IntType(64), 0),
    ])
