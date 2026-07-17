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
    # including one declared later or the struct itself through a pointer
    for struct in program.structs:
        with source_location(line=struct.line, file=struct.file):
            if struct.name in gen.structs:
                raise TypeError(f"struct {struct.name!r} is declared more than once")

            ident = gen.module.context.get_identified_type(struct.name)
            gen.structs[struct.name] = StructInfo(ident, struct.fields)

    # then set each body from the now-resolvable field types
    for struct in program.structs:
        with source_location(line=struct.line, file=struct.file):
            info = gen.structs[struct.name]
            info.type.set_body(*(resolve_type(f.type, gen.structs) for f in struct.fields))
