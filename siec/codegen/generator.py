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


def make_volatile(inst: ir.Instruction) -> ir.Instruction:
    """
    Mark a load or store volatile. llvmlite's printer doesn't know the
    flag, so the instruction renders itself with 'volatile' injected
    after its opcode.
    """
    original = type(inst).descr

    def descr(buf):
        chunk = []
        original(inst, chunk)
        buf.append(chunk[0].replace(f"{inst.opname} ",
                                    f"{inst.opname} volatile ", 1))

    inst.descr = descr
    return inst


@dataclass
class StructInfo:
    """
    A registered struct: its LLVM type plus its ordered fields, for member
    lookup, and its layout and access decorations ('@align(N)', '@volatile').
    """
    type: ir.Type
    fields: list[Field]
    align: int | None = None
    volatile: bool = False

    def field(self, name: str) -> tuple[int, str]:
        """
        Look up a field by name, returning its index and Sie type.
        """
        # an opaque struct, never given a body, has no fields to find
        for index, field in enumerate(self.fields or ()):
            if field.name == name:
                return index, field.type

        raise TypeError(f"struct has no field {name!r}")


@dataclass
class EnumInfo:
    """
    A registered enum: its backing Sie type name plus its evaluated members.
    """
    backing: str
    members: dict[str, int]


class CodeGenerator:
    """
    State shared across the codegen subsystems for one module.
    """

    def __init__(self, module_name: str, target: str | None = None):
        """
        Create an empty LLVM module to generate code into, aimed at the
        given target triple; the host's when none is given.
        """
        from llvmlite import binding

        # the triple decides the target constants and every 'sizeof'
        self.target = target or binding.get_default_triple()

        # a fresh context keeps identified struct types from colliding across modules
        self.module = ir.Module(name=module_name, context=ir.Context())
        self.module.triple = self.target
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

        # the enclosing loops' (break block, continue block, defer depth)
        # targets, innermost last: where a 'break' or 'continue' jumps
        self.loop_targets: list[tuple] = []

        # one loop-stack floor per active defer flush, innermost last: a
        # deferred statement may only steer loops of its own, entered above
        # the floor, never the ones it flushes inside of
        self.flush_loop_floors: list[int] = []

        # the registered 'type' aliases by name, mapped to their canonical
        # expanded targets; every type name is expanded through them
        self.aliases: dict[str, str] = {}

        # the registered '@const' declarations by name, substituted at their uses
        self.constants: dict = {}

        # the registered enums by name, their members evaluated to integers
        self.enums: dict[str, EnumInfo] = {}

        # the '@extern let' globals by name, mapped to their Sie types;
        # their storage lives in the module's globals
        self.globals: dict[str, str] = {}

        # '@static' functions and globals by (file, name), mapped to their
        # module symbols: each file's statics are invisible to every other
        self.statics: dict[tuple[str, str], str] = {}

        # '@symbol' functions by Sie name, mapped to their chosen module
        # symbols, visible everywhere
        self.symbol_names: dict[str, str] = {}

        # the source file whose function body is being emitted, deciding
        # which statics are in view
        self.current_file = ""

    def resolve_symbol(self, name: str) -> str:
        """
        Resolve a Sie name to its module symbol: the current file's static
        when it has one, an '@symbol' mapping next, the public name otherwise.
        """
        if (key := (self.current_file, name)) in self.statics:
            return self.statics[key]

        return self.symbol_names.get(name, name)

    def struct_align(self, type_name: str | None) -> int | None:
        """
        The '@align(N)' a type's allocations must honor; None for types
        without one.
        """
        if type_name is None:
            return None

        info = self.structs.get(type_name.removeprefix("const "))
        return info.align if info is not None else None

    def volatile_struct(self, type_: ir.Type) -> bool:
        """
        Whether an LLVM type is a '@volatile' struct's: loads and stores
        of its values must not be elided or reordered.
        """
        if not isinstance(type_, ir.IdentifiedStructType):
            return False

        info = self.structs.get(type_.name)
        return info is not None and info.volatile


def codegen(program: Program, module_name: str, target: str | None = None) -> ir.Module:
    """
    Generate an LLVM module from a Program AST: register structs, declare functions, emit bodies.
    """
    from siec.codegen.aliases import register_aliases
    from siec.codegen.conditionals import resolve_conditionals
    from siec.codegen.constants import (register_builtin_constants,
                                        register_constants)
    from siec.codegen.enums import register_enums
    from siec.codegen.functions import declare_function, emit_function
    from siec.codegen.globals import register_globals
    from siec.codegen.structs import register_structs

    gen = CodeGenerator(module_name, target)

    # first pass: register the named declarations — aliases first so every
    # later type annotation expands through them, constants next so enum
    # values can reference them, then the '@if' conditions, which those
    # constants decide, splicing in the chosen declarations; enums next so
    # struct fields can be enum-typed, then structs for signatures and
    # bodies to name, and globals last so their types can name any of the above
    register_builtin_constants(gen)
    register_aliases(gen, program)
    register_constants(gen, program)
    resolve_conditionals(gen, program)
    register_enums(gen, program)
    register_structs(gen, program)
    register_globals(gen, program)

    # second pass: declare every function so calls can target ones defined later
    for fn in program.functions:
        declare_function(gen, fn)

    # third pass: emit the bodies of the defined functions, an '@asm'
    # function's assembly standing in for one
    for fn in program.functions:
        if fn.body is not None or fn.asm is not None:
            emit_function(gen, fn)

    return gen.module
