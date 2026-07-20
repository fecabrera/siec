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
    A union's fields share one storage, accessed by reinterpretation.
    """
    type: ir.Type
    fields: list[Field]
    align: int | None = None
    volatile: bool = False
    is_union: bool = False

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

        # the '-g' debug-info builder, None when not emitting debug info
        self.debug = None

        # the Sie return and parameter types of each declared function, for
        # type inference and argument coercion at calls
        self.return_types: dict[str, str | None] = {}
        self.param_types: dict[str, list[str]] = {}
        # same-named generic templates with other type-parameter counts
        self.generic_overloads: dict[str, list] = {}

        # interfaces: declarations, their required actions, what each
        # struct claims, and the claims queued for checking once every
        # method is declared
        self.interfaces: dict = {}
        self.interface_actions: dict = {}
        self.implements: dict[str, set] = {}
        self.pending_conformance: list = []
        self.conformance_ready = False
        # per-symbol parameter defaults with the declaring file, whose
        # view resolves the default expressions at call sites
        self.param_defaults: dict[str, tuple[list, str]] = {}

        # the registered structs by name, for type resolution and member access
        self.structs: dict[str, StructInfo] = {}

        # generic struct templates by name, instantiated by use: each
        # 'S<args>' spelling stamps a concrete struct into 'structs'
        self.generic_structs: dict = {}

        # generic alias templates by name: each 'a<args>' spelling expands
        # the target with its arguments substituted
        self.generic_aliases: dict = {}

        # generic function templates by name; calls declare each 'f<args>'
        # instance once and queue its body for emission
        self.generic_functions: dict = {}
        self.instantiated_functions: set = set()
        self.pending_functions: list = []

        # a generic struct's method templates by (struct, method) name,
        # stamped alongside each 'S<args>' instantiation on first call
        self.generic_methods: dict = {}

        # nonzero while expanding names the compiler wrote itself
        # (substituted generics), which no file's view should gate
        self.ungated_types = 0

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

        # per '@extern' symbol: how each struct parameter travels to C,
        # aligned with the parameters (None marks a direct one), and how a
        # struct return comes back: (kind, coerce type, struct type)
        self.abi_args: dict[str, list] = {}
        self.abi_returns: dict[str, tuple] = {}

        # what each file's 'import's bound: (file, prefix) naming a whole
        # module, (file, name) naming one member; and each module's exports
        self.module_bindings: dict[tuple[str, str], str] = {}
        self.member_bindings: dict[tuple[str, str], str] = {}
        self.module_exports: dict[str, set] = {}

        # the unqualified names each file may use: its own, its includes',
        # its member imports', and the compilation unit's; a file the
        # loader never mapped (a lone parse) sees everything
        self.visible: dict[str, set] = {}
        self.builtin_names: set = set()

        # the source file whose function body is being emitted, deciding
        # which statics are in view
        self.current_file = ""

    def resolve_symbol(self, name: str) -> str:
        """
        Resolve a Sie name to its module symbol: the current file's static
        when it has one, its member imports next, an '@symbol' mapping
        after, the public name otherwise.
        """
        if (key := (self.current_file, name)) in self.statics:
            return self.statics[key]

        name = self.member_bindings.get((self.current_file, name), name)
        return self.symbol_names.get(name, name)

    def resolve_qualified(self, names: list[str]) -> str | None:
        """
        Resolve a dotted 'a.b.name' chain through the current file's module
        bindings: the longest bound prefix claims the chain, its last name
        being the member; None when no prefix is bound.
        """
        for split in range(len(names) - 1, 0, -1):
            prefix = ".".join(names[:split])
            target = self.module_bindings.get((self.current_file, prefix))
            if target is None:
                continue

            # past the prefix there is exactly one member name
            if split != len(names) - 1:
                return None

            member = names[-1]
            exports = self.module_exports.get(target)
            if exports is not None and member not in exports:
                raise TypeError(f"module {prefix!r} has no member {member!r}")

            return self.symbol_names.get(member, member)

        return None

    def sees(self, name: str) -> bool:
        """
        Whether the current file may use a name unqualified: an imported
        module's names need their qualified spelling or a member import.
        """
        names = self.visible.get(self.current_file)
        return names is None or name in names or name in self.builtin_names

    def resolve_callee(self, name: str) -> str | None:
        """
        Resolve a call's name to its module symbol: dotted names through
        the module bindings, plain ones like any other symbol.
        """
        if "." in name:
            return self.resolve_qualified(name.split("."))

        return self.resolve_symbol(name)

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


# builtin declarations every program starts from: 'Result<V, E>' holds a
# value or an error behind its 'ok' tag, 'Result<E>' only the error, and
# 'Ok'/'Error' construct them - usually inferred from the expected type;
# 'Iterator<T>' and 'Iterable<T>' are the interfaces iteration speaks
PRELUDE = """
interface Iterator<T>;

fn Iterator<T>::has_next(&self) -> bool;
fn Iterator<T>::next(&self) -> &T;

interface Iterable<T>;

fn Iterable<T>::iterator(&self) -> Iterator<T>;

struct ArrayIterator<T>: Iterator<T> {
    arr: T[];
    index: u64;
}

fn ArrayIterator<T>::init(&self, arr: T[]) {
    self.arr = arr;
    self.index = 0;
}

fn ArrayIterator<T>::has_next(&self) -> bool {
    return self.index < self.arr.length;
}

fn ArrayIterator<T>::next(&self) -> &T {
    self.index += 1;
    return self.arr[self.index - 1];
}

fn __array_iterator<T>(self: &T[]) -> ArrayIterator<T> {
    return ArrayIterator<T>(self);
}

struct ConstArrayIterator<T> {
    arr: const T[];
    index: u64;
}

fn ConstArrayIterator<T>::has_next(const &self) -> bool {
    return self.index < self.arr.length;
}

fn ConstArrayIterator<T>::next(&self) -> const &T {
    self.index += 1;
    return self.arr[self.index - 1];
}

fn __const_array_iterator<T>(self: const &T[]) -> ConstArrayIterator<T> {
    let it: ConstArrayIterator<T> = { self, 0 };
    return it;
}

struct Result<V, E> {
    ok: bool;
    union {
        value: V;
        error: E;
    };
}

struct Result<E> {
    ok: bool;
    error: E;
}

fn Ok<V, E>(v: V) -> Result<V, E> {
    let r: Result<V, E>;
    r.ok = true;
    r.value = v;
    return r;
}

fn Ok<E>() -> Result<E> {
    let r: Result<E>;
    r.ok = true;
    return r;
}

fn Error<V, E>(e: E) -> Result<V, E> {
    let r: Result<V, E>;
    r.ok = false;
    r.error = e;
    return r;
}

fn Error<E>(e: E) -> Result<E> {
    let r: Result<E>;
    r.ok = false;
    r.error = e;
    return r;
}
"""


def parse_prelude() -> Program:
    """
    Parse the builtin prelude into its declarations.
    """
    from siec.lexer import lex
    from siec.parser import parse

    return parse(lex(PRELUDE))


def codegen(program: Program, module_name: str, target: str | None = None,
            debug: bool = False) -> ir.Module:
    """
    Generate an LLVM module from a Program AST: register structs, declare functions, emit bodies.

    Under 'debug', DWARF metadata rides along: line locations on every
    instruction, and a description of each function and variable.
    """
    from siec.codegen.aliases import register_aliases
    from siec.codegen.conditionals import resolve_conditionals
    from siec.codegen.constants import (register_builtin_constants,
                                        register_constants)
    from siec.codegen.enums import register_enums
    from siec.codegen.functions import declare_function, emit_function
    from siec.codegen.generics import register_generic_function
    from siec.codegen.methods import register_method
    from siec.codegen.globals import register_globals
    from siec.codegen.structs import register_structs

    from siec.codegen.constants import BUILTIN_CONSTANTS

    gen = CodeGenerator(module_name, target)
    if debug:
        from siec.codegen.debug import DebugInfo
        gen.debug = DebugInfo(gen, module_name)

    gen.module_bindings = program.module_bindings
    gen.member_bindings = program.member_bindings
    gen.module_exports = program.module_exports
    gen.visible = program.visible
    gen.builtin_names = set(BUILTIN_CONSTANTS)

    # the builtin prelude's declarations join every program, its names
    # in every file's view
    prelude = parse_prelude()
    program.structs = [*prelude.structs, *program.structs]
    program.functions = [*prelude.functions, *program.functions]
    gen.builtin_names.update(("Result", "Ok", "Error", "Iterator", "Iterable",
                              "ArrayIterator", "ConstArrayIterator"))

    # first pass: register the named declarations - aliases first so every
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

    # second pass: declare every function so calls can target ones defined
    # later; a generic function is a template, declared per instantiation,
    # a method registers under its receiver's rules, an interface receiver
    # marks a required action, and an interface-typed parameter turns the
    # function into a constrained template
    from siec.codegen.interfaces import (adapt_interface_params,
                                         register_action, run_conformance)

    for fn in program.functions:
        if fn.receiver is not None and fn.receiver in gen.interfaces:
            register_action(gen, fn)
            continue

        adapt_interface_params(gen, fn)

        if fn.receiver is not None:
            register_method(gen, fn)
        elif fn.type_params is not None:
            register_generic_function(gen, fn)
        else:
            declare_function(gen, fn)

    # every declaration is in: check each struct's interface claims
    run_conformance(gen)

    # third pass: emit the bodies of the defined functions, an '@asm'
    # function's assembly standing in for one
    for fn in program.functions:
        if (fn.type_params is None and fn.receiver_params is None
                and (fn.body is not None or fn.asm is not None)):
            emit_function(gen, fn)

    # calls met while emitting queue their instantiations' bodies, each of
    # which may queue more: generic functions calling generic functions;
    # their substituted types mix files' names, so no view gates them
    while gen.pending_functions:
        instance = gen.pending_functions.pop(0)
        gen.ungated_types += 1
        try:
            emit_function(gen, instance)
        finally:
            gen.ungated_types -= 1

    return gen.module
