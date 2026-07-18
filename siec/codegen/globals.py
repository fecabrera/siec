"""Registration of '@extern let' global variables."""

from llvmlite import ir

from siec.ast import Program
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator
from siec.codegen.types import is_reference, resolve_type


def register_globals(gen: CodeGenerator, program: Program) -> None:
    """
    Declare every '@extern let' as a module-level global with external
    linkage: its storage is defined and initialized outside this program.
    """
    for glob in program.globals:
        with source_location(line=glob.line, file=glob.file):
            if glob.name in gen.globals or glob.name in gen.module.globals:
                raise TypeError(f"global {glob.name!r} is declared more than once")

            if is_reference(glob.type):
                raise TypeError("a reference cannot type a variable")

            var = ir.GlobalVariable(gen.module, resolve_type(glob.type, gen.structs),
                                    name=glob.name)
            var.linkage = "external"
            gen.globals[glob.name] = glob.type
