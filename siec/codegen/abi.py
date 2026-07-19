"""C ABI lowering of struct arguments to '@extern' functions.

C compilers don't pass small structs as aggregates: they coerce them into
register-shaped values, and pass large ones through memory. LLVM leaves
that lowering to the frontend, so calls into C code must mirror it here.
"""

from llvmlite import ir

from siec.codegen.constants import target_arch
from siec.codegen.generator import CodeGenerator, entry_alloca
from siec.codegen.sizes import target_data

# how one argument travels: as-is, reshaped through memory into a
# register-friendly type, or as a pointer to a copy (byval on x86-64)
DIRECT = ("direct", None)


def scalars(type_) -> list:
    """
    The scalar leaves of an aggregate type, flattened in order.
    """
    if isinstance(type_, (ir.LiteralStructType, ir.IdentifiedStructType)):
        out = []
        for element in type_.elements:
            out += scalars(element)
        return out

    if isinstance(type_, ir.ArrayType):
        return scalars(type_.element) * type_.count

    return [type_]


def scalar_offsets(type_, data, context, base=0) -> list:
    """
    The (byte offset, type) of each scalar leaf, natural C layout.
    """
    if isinstance(type_, (ir.LiteralStructType, ir.IdentifiedStructType)):
        packed = getattr(type_, "packed", False)
        out, offset = [], 0
        for element in type_.elements:
            if not packed:
                align = element.get_abi_alignment(data, context=context)
                offset = (offset + align - 1) // align * align

            out += scalar_offsets(element, data, context, base + offset)
            offset += element.get_abi_size(data, context=context)
        return out

    if isinstance(type_, ir.ArrayType):
        stride = type_.element.get_abi_size(data, context=context)
        out = []
        for i in range(type_.count):
            out += scalar_offsets(type_.element, data, context, base + i * stride)
        return out

    return [(base, type_)]


def is_float(type_) -> bool:
    return isinstance(type_, (ir.FloatType, ir.DoubleType))


def classify(gen: CodeGenerator, type_, is_union: bool) -> tuple:
    """
    How a struct value travels to C on the current target: ('direct', None),
    ('coerce', T), or ('indirect', byval).
    """
    data = target_data(gen.target)
    context = gen.module.context
    size = type_.get_abi_size(data, context=context)
    arch = target_arch(gen.target)

    if arch == "ARCH_AARCH64":
        # a homogeneous float aggregate of up to four elements rides the
        # float registers; a union's overlap disqualifies it
        leaves = scalars(type_)
        if (not is_union and 1 <= len(leaves) <= 4 and is_float(leaves[0])
                and all(leaf == leaves[0] for leaf in leaves)):
            return ("coerce", ir.ArrayType(leaves[0], len(leaves)))

        if size <= 8:
            return ("coerce", ir.IntType(64))

        if size <= 16:
            return ("coerce", ir.ArrayType(ir.IntType(64), 2))

        # larger aggregates pass as a pointer to a caller-made copy
        return ("indirect", False)

    if arch == "ARCH_X86_64":
        if size <= 16:
            # each eightbyte is SSE when every scalar inside it is a
            # float, INTEGER otherwise; a union's overlap reads as bytes
            chunks = []
            for chunk in range((size + 7) // 8):
                inside = [leaf for offset, leaf in
                          scalar_offsets(type_, data, context)
                          if chunk * 8 <= offset < chunk * 8 + 8]
                floats = not is_union and inside and all(map(is_float, inside))
                chunks.append(ir.DoubleType() if floats else ir.IntType(64))

            if len(chunks) == 1:
                return ("coerce", chunks[0])

            return ("coerce", ir.LiteralStructType(chunks))

        # larger aggregates copy onto the stack at the call: byval
        return ("indirect", True)

    # an unclassified target keeps the direct convention and hopes
    return DIRECT


def lower_argument(gen: CodeGenerator, builder: ir.IRBuilder, value,
                   lowering: tuple):
    """
    Reshape one struct argument for C: through a stack spill for a
    coercion, or as the address of a copy for an indirect pass.
    """
    kind, coerce_type = lowering

    if kind == "coerce":
        data = target_data(gen.target)
        context = gen.module.context

        spill = entry_alloca(builder, coerce_type, "abi.coerce")
        spill.align = max(
            coerce_type.get_abi_alignment(data, context=context),
            value.type.get_abi_alignment(data, context=context))

        builder.store(value, builder.bitcast(spill, ir.PointerType(value.type)))
        return builder.load(spill)

    # indirect: the callee reads (its own copy of) the value through a pointer
    copy = entry_alloca(builder, value.type, "abi.copy")
    builder.store(value, copy)
    return copy
