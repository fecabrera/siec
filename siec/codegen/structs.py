"""Registration of struct declarations as LLVM types."""

from ..ast import Program
from .errors import source_location
from .generator import CodeGenerator, StructInfo
from .types import resolve_type


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
            info = gen.structs.get(struct.name)

            if info is None:
                ident = gen.module.context.get_identified_type(struct.name)
                gen.structs[struct.name] = StructInfo(ident, struct.fields)
            elif struct.fields is not None:
                if info.fields is not None:
                    raise TypeError(f"struct {struct.name!r} is declared more than once")

                info.fields = struct.fields

    # then set each body from the now-resolvable field types; a struct
    # never given a body stays opaque, usable only through a pointer
    for struct in program.structs:
        if struct.fields is None:
            continue

        with source_location(line=struct.line, file=struct.file):
            info = gen.structs[struct.name]
            info.type.set_body(*(resolve_type(f.type, gen.structs) for f in struct.fields))
