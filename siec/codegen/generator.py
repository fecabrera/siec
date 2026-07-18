"""Code generation state and entry point."""

from dataclasses import dataclass

from llvmlite import ir

from siec.ast import Field, Program


def entry_alloca(builder: ir.IRBuilder, type_: ir.Type, name: str) -> ir.Instruction:
    """
    Reserve a stack slot in the function's entry block, wherever the builder
    currently is: a slot inside a loop must not re-allocate every iteration.
    """
    entry = builder.function.entry_basic_block

    # in the entry block itself, alloca in place; a second builder would
    # fight the active one over its insertion point
    if builder.block is entry:
        return builder.alloca(type_, name=name)

    # otherwise the entry block is sealed; slot in just before its terminator
    head = ir.IRBuilder(entry)
    if entry.is_terminated:
        head.position_before(entry.terminator)

    return head.alloca(type_, name=name)


@dataclass
class Variable:
    """
    A scoped variable: its stack slot plus the Sie type it was declared with.
    """
    slot: ir.Instruction
    type: str


@dataclass
class StructInfo:
    """
    A registered struct: its LLVM type plus its ordered fields, for member lookup.
    """
    type: ir.Type
    fields: list[Field]

    def field(self, name: str) -> tuple[int, str]:
        """
        Look up a field by name, returning its index and Sie type.
        """
        # an opaque struct, never given a body, has no fields to find
        for index, field in enumerate(self.fields or ()):
            if field.name == name:
                return index, field.type

        raise TypeError(f"struct has no field {name!r}")


class CodeGenerator:
    """
    State shared across the codegen subsystems for one module.
    """

    def __init__(self, module_name: str):
        """
        Create an empty LLVM module to generate code into.
        """
        # a fresh context keeps identified struct types from colliding across modules
        self.module = ir.Module(name=module_name, context=ir.Context())
        self.str_count = 0

        # the Sie return and parameter types of each declared function, for
        # type inference and argument coercion at calls
        self.return_types: dict[str, str | None] = {}
        self.param_types: dict[str, list[str]] = {}

        # the registered structs by name, for type resolution and member access
        self.structs: dict[str, StructInfo] = {}

        # the enclosing block expressions' (slot, end block, Sie type, defer
        # depth) targets, innermost last: what an 'emit' stores into and jumps to
        self.emit_targets: list[tuple] = []

        # one frame of deferred (statement, scope) pairs per open scope,
        # innermost last: what runs when each scope ends
        self.defer_frames: list[list] = []

        # nonzero while deferred statements are being flushed, where a
        # 'return' or 'emit' would flush the very frame holding it
        self.flushing_defers = 0

        # the registered '@const' declarations by name, substituted at their uses
        self.constants: dict = {}


def codegen(program: Program, module_name: str) -> ir.Module:
    """
    Generate an LLVM module from a Program AST: register structs, declare functions, emit bodies.
    """
    from siec.codegen.constants import register_constants
    from siec.codegen.functions import declare_function, emit_function
    from siec.codegen.structs import register_structs

    gen = CodeGenerator(module_name)

    # first pass: register structs so function signatures and bodies can name
    # them, then constants so bodies can substitute their values
    register_structs(gen, program)
    register_constants(gen, program)

    # second pass: declare every function so calls can target ones defined later
    for fn in program.functions:
        declare_function(gen, fn)

    # third pass: emit the bodies of the defined functions
    for fn in program.functions:
        if fn.body is not None:
            emit_function(gen, fn)

    return gen.module
