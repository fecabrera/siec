"""Registration of struct declarations as LLVM types."""

from llvmlite import ir

from siec.ast import Program
from siec.codegen.aliases import expand_alias
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator, StructInfo
from siec.codegen.sizes import target_data
from siec.codegen.types import is_reference, resolve_type


def register_structs(gen: CodeGenerator, program: Program) -> None:
    """
    Register every struct as an identified LLVM type, then fill in its body.
    """
    # first create empty identified types so a field may name any struct,
    # including one declared later or the struct itself through a pointer;
    # a bodiless forward declaration registers the type alone, taking its
    # fields from the definition when one appears
    for struct in program.structs:
        with source_location(line=struct.line, file=struct.file):
            gen.current_file = struct.file

            # a generic struct is a template: nothing registers until a
            # concrete 'S<args>' spelling instantiates it
            if struct.params is not None:
                template = gen.generic_structs.get(struct.name)
                if template is None:
                    gen.generic_structs[struct.name] = struct
                elif struct.fields is not None:
                    if template.fields is not None:
                        raise TypeError(f"struct {struct.name!r} is declared "
                                        "more than once")

                    template.fields = struct.fields
                continue

            info = gen.structs.get(struct.name)

            if info is None:
                ident = gen.module.context.get_identified_type(struct.name)
                gen.structs[struct.name] = info = StructInfo(ident, struct.fields)
            elif struct.fields is not None:
                if info.fields is not None:
                    raise TypeError(f"struct {struct.name!r} is declared more than once")

                info.fields = struct.fields

            # decorators apply from whichever declaration carries them
            if struct.packed:
                info.type.packed = True

            if struct.align is not None:
                info.align = struct.align

            if struct.volatile:
                info.volatile = True

            if struct.is_union:
                info.is_union = True

    # then set each body from the now-resolvable field types; a struct
    # never given a body stays opaque, usable only through a pointer
    for struct in program.structs:
        if struct.fields is None or struct.params is not None:
            continue

        with source_location(line=struct.line, file=struct.file):
            gen.current_file = struct.file

            # references only pass parameters; a field is its own storage
            for field in struct.fields:
                field.type = expand_alias(gen, field.type)
                if is_reference(field.type):
                    raise TypeError(f"field {field.name!r} cannot be a reference")

            info = gen.structs[struct.name]

            # a union's fields share storage: no single member's default
            # could fill it
            if info.is_union and any(f.default is not None for f in struct.fields):
                raise TypeError(f"a union field cannot have a default value")
            resolved = [resolve_type(f.type, gen.structs) for f in struct.fields]

            # a union's fields share one storage: the most-aligned field's
            # type carries the alignment, padding bytes reach the largest
            if info.is_union:
                resolved = union_storage(gen, resolved)

            info.type.set_body(*resolved)


def union_storage(gen: CodeGenerator, field_types: list) -> list:
    """
    The members backing a union: its most-aligned (then largest) field's
    type, padded with bytes up to the size of its largest.

    A scalar dominant backs the union directly. An aggregate one carries
    padding, and in a union those padding bytes are live — another
    member's value sits in them — but a value copy of a struct moves its
    fields, not its padding. Aggregate-led unions store as an array of
    alignment-sized integers instead, so every byte survives a copy.
    """
    data = target_data(gen.target)
    context = gen.module.context

    def measure(type_):
        return (type_.get_abi_alignment(data, context=context),
                type_.get_abi_size(data, context=context))

    dominant = max(field_types, key=measure)
    size = max(type_.get_abi_size(data, context=context) for type_ in field_types)

    if isinstance(dominant, (ir.LiteralStructType, ir.IdentifiedStructType,
                             ir.ArrayType)):
        align = dominant.get_abi_alignment(data, context=context)
        unit = align if align in (1, 2, 4, 8) else 8
        padded = -(-size // unit) * unit
        return [ir.ArrayType(ir.IntType(unit * 8), padded // unit)]

    padding = size - dominant.get_abi_size(data, context=context)
    if padding > 0:
        return [dominant, ir.ArrayType(ir.IntType(8), padding)]

    return [dominant]
