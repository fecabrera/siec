"""Code generation state and entry point."""

import copy
from collections import deque
from dataclasses import dataclass
from functools import lru_cache

from llvmlite import ir

from siec.ast import Field, Function, Program


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
    volatile: bool = False
    moved: bool = False
    # runtime ownership bit for a value whose type implements Destroy
    drop_flag: ir.Instruction | None = None


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
    A registered struct's semantic fields, representation, and decorations.

    ``type`` remains None through collection, resolution, and checking. The
    backend attaches its LLVM representation only after semantics complete.
    A union's fields share one storage, accessed by reinterpretation.
    """
    type: ir.Type | None
    fields: list[Field]
    align: int | None = None
    volatile: bool = False
    is_union: bool = False
    packed: bool = False
    literal: bool = False
    backing: str | None = None

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

        # the triple decides the target constants and every '@sizeof'
        self.target = target or binding.get_default_triple()

        # a fresh context keeps identified struct types from colliding across modules
        self.module = ir.Module(name=module_name, context=ir.Context())
        self.module.triple = self.target
        self.types_lowered = False
        self.functions_lowered = False
        self.semantic_complete = False
        self.declaration_inventory_complete = False
        self.generic_type_depth = 0
        self.str_count = 0
        self.temporary_count = 0
        self.string_pool: dict[str, ir.GlobalVariable] = {}
        # Unchosen '@if' branch spans, by source file, for editor semantic
        # highlighting. Code generation records the same choices it compiles.
        self.inactive_regions: dict[str, list[tuple[int, int, int, int]]] = {}

        # Code generation's private working AST. Its passes rewrite types,
        # conditionals, and expansions without touching the caller's tree.
        self.program: Program | None = None

        # the '-g' debug-info builder, None when not emitting debug info
        self.debug = None

        # the Sie return and parameter types of each declared function, for
        # type inference and argument coercion at calls
        self.return_types: dict[str, str | None] = {}
        self.param_types: dict[str, list[str]] = {}
        # Canonical callable declarations resolved without LLVM. The backend
        # lowers this inventory only after semantic checking has completed.
        self.resolved_functions: dict[str, Function] = {}
        self.function_signatures: dict[str, tuple] = {}
        # Raw callable declarations are collected before their type-bearing
        # headers resolve. The written-name index is the declaration-phase
        # view; later registries hold canonical resolved symbols.
        self.raw_callables: dict[str, list[Function]] = {}
        self.callable_declarations: list[Function] = []
        self.collected_callables: set[int] = set()
        self.callable_inventory_complete = False
        self.callables_resolved = False
        # the symbols declared '@noreturn', whose bodies must not return
        self.noreturns: set[str] = set()
        # each overloaded name's candidates: (signature key, symbol) pairs,
        # in declaration order; calls pick among them by argument types
        self.overloads: dict[str, list[tuple[tuple, str]]] = {}
        # a generic struct's stamped overload bodies, waiting for a call
        # to pick them: a candidate that fits only some element types
        # stays a bodiless declaration everywhere else
        self.deferred_overloads: dict[str, object] = {}
        # same-named generic templates with other type-parameter counts
        self.generic_overloads: dict[str, list] = {}

        # interfaces: declarations, their required actions, what each
        # struct claims, and the claims queued for checking once every
        # method is declared
        self.interfaces: dict = {}
        self.interface_actions: dict = {}
        self.implements: dict[str, set] = {}
        self.interface_queries: set[tuple[str, str]] = set()
        self.pending_conformance: deque[tuple] = deque()
        self.resolved_conformance: deque[tuple] = deque()
        # Raw extension declarations have an explicit collect/resolve/check
        # lifecycle. Type-dependent conditional branches may add declarations
        # until callable collection freezes; each declaration advances once.
        self.extension_declarations: list = []
        self.collected_extensions: set[int] = set()
        self.resolved_extensions: set[int] = set()
        self.checked_extensions: set[int] = set()
        # the arrays' '@extend T[]' claims: (element placeholder,
        # interface spelling, bounds, declaring file), substituted and
        # filtered by their bounds per element on query
        self.array_claims: list[tuple[str, str, dict | None, str]] = []
        # blanket claims over a bare receiver placeholder:
        # (placeholder, interface spellings, bounds, declaring file)
        self.generic_claims: list[tuple[str, list[str], dict | None, str]] = []
        # Claims added to a generic struct by '@extend Base<T>' retain their
        # own bounds. Each concrete instance publishes only the claims whose
        # extension environment accepts its arguments.
        self.generic_struct_claims: dict[str, list[tuple]] = {}
        # per-symbol parameter defaults with the declaring file, whose
        # view resolves the default expressions at call sites
        self.param_defaults: dict[str, tuple[list, str]] = {}

        # symbols whose last parameter is the 'args...' Any[] sugar;
        # their calls pack extra arguments into it
        self.variadics: set[str] = set()
        self.var_args: set[str] = set()

        # every type name wrapped 'as Any', by id: the runtime
        # '@typename' table, emitted once emission has seen every wrap
        self.any_names: dict[int, str] = {}
        self.typename_fn = None

        # the registered structs by name, for type resolution and member access
        self.structs: dict[str, StructInfo] = {}

        # generic struct templates by name, instantiated by use: each
        # 'S<args>' spelling stamps a concrete struct into 'structs'
        self.generic_structs: dict = {}

        # generic alias templates by name: each 'a<args>' spelling expands
        # the target with its arguments substituted
        self.generic_aliases: dict = {}

        # Alias syntax is collected separately from canonical targets. This
        # keeps collection from asking what a target means while still making
        # every alias identity visible to collision checks.
        self.alias_declarations: list = []
        self.collected_aliases: set[int] = set()
        self.alias_targets: dict[str, str] = {}
        self.resolved_aliases: set[int] = set()

        # generic function templates by name; calls declare each 'f<args>'
        # instance once and queue its body for emission
        self.generic_functions: dict = {}
        self.instantiated_functions: set = set()
        self.pending_functions: deque[Function] = deque()
        # Each concrete generic function or receiver-family method follows
        # the same semantic lifecycle. Calls request instances, signature
        # resolution queues them, and the fixed-point checker marks their
        # bodies checked before final backend work consumes the complete set.
        self.function_instance_states: dict[str, str] = {}
        self.checked_functions: set[str] = set()
        self.checked_instance_bodies: list[Function] = []
        self.checked_default_types: set[str] = set()
        self.checking_function: Function | None = None
        self.checking_loop_depth = 0
        # the concrete callee the last checked call resolved to, letting a
        # call statement's path end when that callee never returns
        self.checked_call: str | None = None
        self.emitting = False
        # concrete definitions displaced by an '@override' declaration;
        # registration keeps their signatures, emission skips their bodies
        self.overridden_functions: set[int] = set()
        # exact method specializations suppress only the matching receiver
        # family overload, leaving its siblings available
        self.overridden_method_signatures: dict[str, set[tuple]] = {}

        # a generic struct's method templates by (struct, method) name,
        # stamped alongside each 'S<args>' instantiation on first call
        self.generic_methods: dict = {}
        # methods over a bare receiver placeholder, such as a bounded
        # '@extend<T: Scalar> T' block, grouped by method name
        self.generic_receiver_methods: dict[str, list] = {}
        # '@private' methods by canonical 'Type::method' name. Method
        # lookup starts from a carried receiver type rather than an import
        # spelling, so it needs an explicit textual-module visibility gate.
        self.private_methods: dict[str, set[str]] = {}

        # nonzero while expanding names the compiler wrote itself
        # (substituted generics), which no file's view should gate
        self.ungated_types = 0

        # the enclosing block expressions' (slot, end block, Sie type, defer
        # depth) targets, innermost last: what an 'emit' stores into and jumps to
        self.emit_targets: list[tuple] = []

        # one frame of deferred (statement, scope) pairs per open scope,
        # innermost last: what runs when each scope ends
        self.defer_frames: list[list] = []

        # borrowed destructible rvalues materialized by each active call;
        # the call destroys them in reverse argument-evaluation order
        self.borrowed_temporary_frames: list[list] = []

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

        # Resolved non-generic aliases by name, mapped to canonical targets.
        self.aliases: dict[str, str] = {}

        # the registered '@const' declarations by name, substituted at their uses
        self.constants: dict = {}
        self.resolved_constants: set[int] = set()
        self.constants_resolved = False

        # the registered '@const' macros by name, expanded at their calls
        self.macros: dict = {}

        # '@deprecated' functions by symbol, mapped to their advice; the
        # call graph and the uses met while emitting decide which of them
        # warn, once the whole program is in
        self.deprecated: dict[str, str] = {}
        self.call_graph: dict = {}
        # each direct edge's first source location: compile errors in lazily
        # emitted generic bodies use these to reconstruct a Sie call trace
        self.call_sites: dict[tuple[str, str], tuple[str, int]] = {}
        # generic structs may stamp interface methods without a direct call.
        # Retain the type-use and resulting method edges so their errors can
        # still explain what caused those bodies to be emitted.
        self.type_instantiation_sites: dict[
            str, tuple[str, str, int]
        ] = {}
        self.instantiation_sites: dict[
            str, tuple[str, str, int]
        ] = {}
        self.deprecated_uses: list = []

        # '@remove' functions by symbol, mapped to their advice: a
        # declaration stands so uses name it, and each use fails
        self.removed: dict[str, str] = {}

        # the function whose body is being emitted, and the line of the
        # statement inside it: where a use of a deprecated name sits
        self.current_function: str | None = None
        self.current_line: int = 0

        # Enum syntax and identities are collected before backing types and
        # member values resolve. The member map is the dependency inventory.
        self.enums: dict[str, EnumInfo] = {}
        self.enum_declarations: list = []
        self.collected_enums: set[int] = set()
        self.enum_member_declarations: dict[tuple[str, str], tuple] = {}
        self.resolved_enums: set[int] = set()

        # the '@extern let' globals by name, mapped to their Sie types;
        # their storage lives in the module's globals
        self.globals: dict[str, str] = {}
        self.resolved_globals: dict[str, object] = {}

        # '@static' functions and globals by (file, name), mapped to their
        # module symbols: each file's statics are invisible to every other
        self.statics: dict[tuple[str, str], str] = {}

        # '@symbol' functions by Sie name, mapped to their chosen module
        # symbols, visible everywhere; the declaring file rides along so
        # a qualified member only maps through its own module's binding
        self.symbol_names: dict[str, str] = {}
        self.symbol_files: dict[str, str] = {}

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

        # the loader's finer-grained view, resolving WHICH declaration a
        # name in view means when modules collide: each file with its
        # includes, each member import's module, and the entry sources;
        # all empty for a bare program, which is one namespace
        self.include_closure: dict[str, set] = {}
        self.member_targets: dict[tuple[str, str], tuple] = {}
        self.entry_files: list = []

        # under separate compilation, the files whose definitions this
        # unit owns - the sources and their includes; None (a whole-program
        # build) defines every file's
        self.unit_files: set | None = None

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

    def string_constant(self, text: str) -> ir.GlobalVariable:
        """
        Return one private, null-terminated global for a string literal,
        sharing repeated text throughout the module.
        """
        if text in self.string_pool:
            return self.string_pool[text]

        data = text.encode() + b"\0"
        array_type = ir.ArrayType(ir.IntType(8), len(data))
        const = ir.GlobalVariable(self.module, array_type,
                                  name=f".str.{self.str_count}")
        const.global_constant = True
        const.linkage = "private"
        const.initializer = ir.Constant(array_type, bytearray(data))

        self.str_count += 1
        self.string_pool[text] = const
        return const

    def resolve_qualified(self, names: list[str]) -> str | None:
        """
        Resolve a dotted 'a.b.name' chain through the current file's module
        bindings: the longest bound prefix claims the chain, its last name
        being the member; None when no prefix is bound.
        """
        found = self.resolve_member(names)
        return found[0] if found is not None else None

    def resolve_member(self, names: list[str]) -> tuple[str, str] | None:
        """
        Like resolve_qualified, but paired with the module file the
        chain reached, for lookups that must know WHICH module's
        declaration a member names.
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

            # a '@symbol' mapping applies only when its declaration is the
            # module's own: another module's same-named binding (libc's
            # 'stderr', say) must not hijack this member
            symbol = self.symbol_names.get(member, member)
            if symbol != member:
                origin = self.symbol_files.get(member)
                if origin not in self.include_closure.get(target, {target}):
                    symbol = member

            return symbol, target

        return None

    def defines(self, file: str | None) -> bool:
        """
        Whether this unit defines a file's functions: every file's in a
        whole-program build; the sources' and their includes' under
        separate compilation, where an imported module's definitions
        belong to its own unit and only its declarations join this one.
        """
        return (self.unit_files is None or file is None
                or file in self.unit_files)

    def sees(self, name: str) -> bool:
        """
        Whether the current file may use a name unqualified: an imported
        module's names need their qualified spelling or a member import.
        """
        names = self.visible.get(self.current_file)
        return names is None or name in names or name in self.builtin_names

    def sees_private_from(self, file: str | None) -> bool:
        """
        Whether the current file and a declaration belong to one textual
        module: the same file, or files joined under an '@include' root.
        """
        if (file is None or not self.current_file
                or not self.include_closure
                or file == self.current_file):
            return True

        return any(
            file in closure and self.current_file in closure
            for closure in self.include_closure.values()
        )

    def sees_method(self, symbol: str) -> bool:
        """Whether a resolved method name is public or textually private."""
        origins = self.private_methods.get(symbol)
        return (not origins
                or any(self.sees_private_from(file) for file in origins))

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
# 'Iterator<T>' and 'Iterable<T>' are the interfaces iteration speaks,
# and 'Add<S, T>' and its siblings the ones the binary operators do;
# 'GetItem<K, V>' and 'SetItem<K, V>' describe indexed access
PRELUDE = """
// a sealed marker implemented by the compiler's primitive value types;
// unlike an ordinary interface, user declarations cannot claim it
interface Scalar;

// Clone constructs a new value from a borrowed value of the same concrete
// type. Assignment uses it only when no specialized AssignFrom<Self> exists.
interface Clone;

fn Clone::clone(const &self) -> Self;

// Borrowed assignment preserves its source; consuming assignment takes it.
interface AssignFrom<T>;

fn AssignFrom<T>::assign_from(&self, source: const &T);

interface Assign<T>;

fn Assign<T>::assign(&self, source: T);

// Destroy releases an owned value's resources. The compiler invokes it
// exactly once when that ownership reaches the end of its lifetime.
interface Destroy;

fn Destroy::destroy(&self);

interface Iterator<T>;

fn Iterator<T>::has_next(&self) -> bool;
fn Iterator<T>::next(&self) -> &T;

// a const iteration hands out const references: the iterator itself
// advances, the collection stays untouched
interface ConstIterator<T>;

fn ConstIterator<T>::has_next(&self) -> bool;
fn ConstIterator<T>::next(&self) -> const &T;

// an Iterable iterates both ways: 'iterator()' serves a mutable value,
// 'const_iterator()' a const one, and 'foreach' picks by the source
interface Iterable<T>;

fn Iterable<T>::iterator(&self) -> Iterator<T>;
fn Iterable<T>::const_iterator(const &self) -> ConstIterator<T>;

// the operator interfaces: 'a + b' on a struct operand is the 'a.add(b)'
// shorthand, and claiming 'Add<S, T>' declares that shorthand's contract
interface Add<S, T>;

fn Add<S, T>::add(&self, value: const T) -> S;

interface Sub<S, T>;

fn Sub<S, T>::sub(&self, value: const T) -> S;

interface Mul<S, T>;

fn Mul<S, T>::mul(&self, value: const T) -> S;

interface Div<S, T>;

fn Div<S, T>::div(&self, value: const T) -> S;

interface Rem<S, T>;

fn Rem<S, T>::rem(&self, value: const T) -> S;

// the compound assignment interfaces: 'a += b' on a struct operand is
// the 'a.add_assign(b)' shorthand, which updates 'a' in place instead of
// assigning an operator's result back over it
interface AddAssign<T>;

fn AddAssign<T>::add_assign(&self, value: const T);

interface SubAssign<T>;

fn SubAssign<T>::sub_assign(&self, value: const T);

interface MulAssign<T>;

fn MulAssign<T>::mul_assign(&self, value: const T);

interface DivAssign<T>;

fn DivAssign<T>::div_assign(&self, value: const T);

interface RemAssign<T>;

fn RemAssign<T>::rem_assign(&self, value: const T);

// equality: 'a == b' on a struct operand is the 'a.eq(b)' shorthand,
// and 'a != b' its negation; claiming 'Eq<T>' declares the contract
interface Eq<T>;

fn Eq<T>::eq(const &self, value: const T) -> bool;

// ordering: one 'cmp' serves '<', '>', '<=', and '>=', each comparing
// its sign: 'a < b' is 'a.cmp(b) < 0'; claiming 'Ord<T>' declares it
interface Ord<T>;

fn Ord<T>::cmp(const &self, value: const T) -> i32;

// indexed access: 'a[key]' is 'a.get_item(key)' on a struct, and
// 'a[key] = value' is 'a.set_item(key, value)'; native arrays, pointers,
// and tuples keep their built-in indexing
interface GetItem<K, V>;

fn GetItem<K, V>::get_item(const &self, key: const K) -> const V;

interface SetItem<K, V>;

fn SetItem<K, V>::set_item(&self, key: const K, value: V);

/**
 * Raw storage with T's layout and no automatic initialized lifetime.
 */
struct Slot<T> {
    value: T;
}

/** Move an owned value into uninitialized storage. */
fn Slot<T>::write(&self, value: T) {}

/** Copy a plain value, or clone an owned value, into uninitialized storage. */
fn Slot<T>::write_from(&self, value: const &T) {}

/** Move the initialized value out and leave the storage uninitialized. */
fn Slot<T>::take(&self) -> T { return self.value; }

/** Borrow the initialized value. */
fn Slot<T>::get(const &self) -> const &T { return &self.value; }

/** Mutably borrow the initialized value. */
fn Slot<T>::get_mut(&self) -> &T { return &self.value; }

/** Apply borrowed assignment to an existing target. */
fn Slot<T>::assign_to(const &self, target: &T) {}

/** Destroy the initialized value and leave the storage uninitialized. */
fn Slot<T>::drop(&self) {}

/** Destroy the initialized value and move a replacement into its storage. */
fn Slot<T>::replace(&self, value: T) {}

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

struct ConstArrayIterator<T>: ConstIterator<T> {
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

// an array is an iterable of its element, by definition
fn T[]::iterator(&self) -> ArrayIterator<T> {
    return ArrayIterator<T>(self);
}

fn T[]::const_iterator(const &self) -> ConstArrayIterator<T> {
    let it: ConstArrayIterator<T> = { self, 0 };
    return it;
}

@extend T[]: Iterable<T>;

struct Any {
    id: u64;
    data: opaque*;
}

struct Enumerated<T> {
    index: u64;
    value: T;
}

struct ConstEnumerated<T> {
    index: u64;
    value: const T;
}

struct EnumerateIterator<I, T>: Iterator<Enumerated<T>> {
    inner: I;
    count: u64;
    current: Enumerated<T>;
}

fn EnumerateIterator<I, T>::has_next(&self) -> bool {
    return self.inner.has_next();
}

fn EnumerateIterator<I, T>::next(&self) -> &Enumerated<T> {
    self.current = { self.count, self.inner.next() };
    self.count += 1;
    return self.current;
}

struct ConstEnumerateIterator<I, T>: ConstIterator<ConstEnumerated<T>> {
    inner: I;
    count: u64;
    current: ConstEnumerated<T>;
}

fn ConstEnumerateIterator<I, T>::has_next(&self) -> bool {
    return self.inner.has_next();
}

fn ConstEnumerateIterator<I, T>::next(&self) -> const &ConstEnumerated<T> {
    self.current = { self.count, self.inner.next() };
    self.count += 1;
    return self.current;
}

fn __enumerate<I, T>(it: I) -> EnumerateIterator<I, T> {
    let e: EnumerateIterator<I, T>;
    e.inner = it;
    e.count = 0;
    return e;
}

fn __const_enumerate<I, T>(it: I) -> ConstEnumerateIterator<I, T> {
    let e: ConstEnumerateIterator<I, T>;
    e.inner = it;
    e.count = 0;
    return e;
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

PRELUDE_FILE = "<prelude>"


@lru_cache(maxsize=1)
def _prelude_template() -> Program:
    """
    Parse and source-tag the builtin prelude once into an immutable template.

    Its declarations carry ordinary line numbers. Giving those lines their
    own virtual file keeps a codegen error in a generic prelude body from
    being reported against the command-line source instead.
    """
    from siec.lexer import lex
    from siec.parser import parse

    program = parse(lex(PRELUDE))
    for declaration in (
            *program.structs,
            *program.functions,
            *program.consts,
            *program.enums,
            *program.globals,
            *program.aliases,
            *program.extends,
            *program.errors,
            *program.asserts):
        declaration.file = PRELUDE_FILE

    return program


def parse_prelude() -> Program:
    """
    A private working copy of the cached builtin prelude.

    Registration mutates type spellings and generic templates, so each
    compilation receives its own clone while lexing and parsing happen once.
    """
    return copy.deepcopy(_prelude_template())


def codegen(program: Program, module_name: str, target: str | None = None,
            debug: bool = False, define_imports: bool = True,
            gen: "CodeGenerator | None" = None) -> ir.Module:
    """
    Generate an LLVM module from a Program AST: register structs, declare functions, emit bodies.

    Under 'debug', DWARF metadata rides along: line locations on every
    instruction, and a description of each function and variable.

    Without 'define_imports' - separate compilation - an imported module's
    functions stay declarations, defined when the module compiles as its
    own unit; only the sources and their includes define here.

    A caller may pass its own generator to emit into: its tables hold
    everything the program declared afterwards - however far emission
    got - which is what the language server reads. The generator also
    retains the private working copy of the AST; codegen never mutates the
    Program instance supplied by its caller.
    """
    from siec.codegen.aliases import collect_aliases, resolve_aliases
    from siec.codegen.conditionals import check_asserts, resolve_conditionals
    from siec.codegen.constants import (
        register_builtin_constants,
        register_constants,
        resolve_constants,
    )
    from siec.codegen.enums import collect_enums, resolve_enums
    from siec.codegen.callables import (
        collect_callables,
        complete_callable_inventory,
        resolve_callables,
    )
    from siec.codegen.functions import emit_function, lower_functions
    from siec.codegen.globals import lower_globals, resolve_globals
    from siec.codegen.structs import declare_structs, define_structs

    from siec.codegen.constants import BUILTIN_CONSTANTS

    # Registration and emission deliberately rewrite the AST: conditionals
    # splice declarations, aliases canonicalize annotations, and expansions
    # attach generated nodes. Keep all of that inside a private working tree.
    program = copy.deepcopy(program)

    gen = gen or CodeGenerator(module_name, target)
    gen.program = program

    gen.module_bindings = program.module_bindings
    gen.member_bindings = program.member_bindings
    gen.member_targets = program.member_targets
    gen.module_exports = program.module_exports
    gen.visible = program.visible
    gen.include_closure = program.include_closure
    gen.entry_files = program.entry_files
    gen.builtin_names = set(BUILTIN_CONSTANTS)

    if not define_imports:
        gen.unit_files = program.unit_files

    # the builtin prelude's declarations join every program, its names
    # in every file's view
    prelude = parse_prelude()
    program.structs = [*prelude.structs, *program.structs]
    program.functions = [*prelude.functions, *program.functions]
    program.extends = [*prelude.extends, *program.extends]
    gen.builtin_names.update(struct.name for struct in prelude.structs)
    gen.builtin_names.update(("Result", "Ok", "Error", "Scalar", "Clone",
                              "AssignFrom", "Assign", "Destroy",
                              "Iterator", "Iterable",
                              "ConstIterator", "GetItem", "SetItem",
                              "ArrayIterator", "ConstArrayIterator",
                              "Enumerated", "ConstEnumerated",
                              "EnumerateIterator", "ConstEnumerateIterator",
                              "__enumerate", "__const_enumerate",
                              "Tuple", "Any"))

    # Parsing is complete before codegen receives the Program. Choose every
    # condition that needs no type meaning first; enum members, '@sizeof',
    # and '@typeid' wait for the active type inventory below.
    register_builtin_constants(gen)
    collect_aliases(gen, program)
    register_constants(gen, program)
    resolve_conditionals(gen, program, defer_types=True)

    # Collection inventories every active type/interface and callable
    # identity, plus extension claims, without resolving struct fields or
    # callable headers. Nothing in the following resolution phase may depend
    # on source or module traversal order.
    collect_callables(gen, program)
    collect_enums(gen, program)
    declare_structs(gen, program)

    from siec.codegen.interfaces import collect_extensions, resolve_extensions

    collect_extensions(gen, program)

    # Resolve declaration headers only after the active inventory is present.
    # Claims resolve before fields so bounded fields can use an extension
    # declared anywhere in the compilation unit.
    resolve_extensions(gen)
    resolve_enums(gen)
    resolve_aliases(gen)
    define_structs(gen, program)

    def register_conditional_branch(branch) -> None:
        collect_callables(gen, branch)
        collect_enums(gen, branch)
        declare_structs(gen, branch)
        collect_extensions(gen, branch)
        resolve_extensions(gen)
        resolve_enums(gen)
        resolve_aliases(gen)
        define_structs(gen, branch)

    resolve_conditionals(
        gen,
        program,
        register_branch=register_conditional_branch,
    )

    # Every selected branch has now contributed its raw declarations. Verify
    # and freeze the inventories before resolving globals or callable headers.
    from siec.codegen.declarations import complete_declaration_inventory

    complete_declaration_inventory(gen, program)
    complete_callable_inventory(gen, program)

    # Resolve every generic/interface type header against the complete active
    # inventory, even when no body or expression uses it.
    from siec.codegen.headers import resolve_type_declaration_headers

    resolve_type_declaration_headers(gen, program)

    # Globals join the resolved value inventory before constants, since a
    # constant '@sizeof' or '@typeid' may name one. Then resolve every constant
    # target regardless of whether a later expression uses its value.
    resolve_globals(gen, program)
    resolve_constants(gen)

    from siec.codegen.interfaces import (
        check_extensions,
        resolve_extension_methods,
        resolve_conformance,
        run_conformance,
    )

    resolve_extension_methods(gen)
    resolve_callables(gen)

    from siec.codegen.overrides import validate_concrete_overrides

    validate_concrete_overrides(gen, program)

    # Checking is last: every resolved field and callable signature is
    # available before assertions, receiver-family requirements, and nominal
    # interface conformance decide whether the collected declarations agree.
    check_asserts(gen, program)

    check_extensions(gen)

    # every declaration is in: check each struct's interface claims
    resolve_conformance(gen)
    run_conformance(gen)

    # Check ordinary bodies against the resolved inventories. Generic calls
    # request concrete headers and bodies for the fixed-point worklist below.
    from siec.codegen.checking import check_function

    for fn in program.functions:
        if (fn.type_params is None and fn.receiver_params is None
                and (fn.body is not None or fn.asm is not None)
                and id(fn) not in gen.overridden_functions
                and (gen.defines(fn.file) or fn.is_static or fn.is_inline)):
            check_function(gen, fn)

    # Calls met while checking bodies request concrete generic instances.
    # Resolve their headers, claims, bounds, and bodies to a fixed point.
    from siec.codegen.worklist import run_semantic_worklist

    run_semantic_worklist(gen)

    complete_semantics(gen)

    # The semantic graph is complete and checked. Only now materialize its
    # LLVM globals and callable declarations, then lower checked bodies.
    from siec.codegen.structs import lower_structs

    lower_structs(gen)
    gen.types_lowered = True
    lower_globals(gen)
    lower_functions(gen)
    gen.functions_lowered = True

    # Debug metadata belongs to the emitted artifact too. Construct it only
    # after the complete semantic program has crossed the check boundary.
    if debug:
        from siec.codegen.debug import DebugInfo

        gen.debug = DebugInfo(gen, module_name)

    gen.emitting = True

    for fn in program.functions:
        if (fn.type_params is None and fn.receiver_params is None
                and (fn.body is not None or fn.asm is not None)
                and id(fn) not in gen.overridden_functions
                and (gen.defines(fn.file) or fn.is_static or fn.is_inline)):
            emit_function(gen, fn)

    from siec.codegen.functions import link_once

    for instance in gen.checked_instance_bodies:
        gen.ungated_types += 1
        try:
            emit_function(gen, instance)
        finally:
            gen.ungated_types -= 1

        if gen.unit_files is not None:
            link_once(gen, instance)

    # a stamped '@inline' overload no call activated keeps no body; only a
    # definition may carry 'linkonce_odr', so it declares externally
    for func in gen.module.functions:
        if func.linkage == "linkonce_odr" and not func.blocks:
            func.linkage = "external"

    gen.emitting = False

    # every wrap has been seen: the runtime '@typename' table can build
    from siec.codegen.expressions import finish_typename_table

    finish_typename_table(gen)

    # the whole call graph is in: the uses of deprecated functions the
    # program can reach warn now
    from siec.codegen.deprecation import report_deprecations

    report_deprecations(gen)

    return gen.module


def complete_semantics(gen: CodeGenerator) -> None:
    """
    Close the checked semantic graph before the first output-IR mutation.

    Target layouts are validated from semantic field records, including
    concrete generic instances discovered by body checking. Any LLVM value or
    identified type already present here is a phase-boundary violation.
    """
    from siec.codegen.sizes import type_layout

    if not gen.callable_inventory_complete or not gen.callables_resolved:
        raise RuntimeError(
            "callable declarations did not cross collection and resolution")
    if not gen.declaration_inventory_complete:
        raise RuntimeError("declaration inventory was not frozen")
    if not gen.constants_resolved:
        raise RuntimeError(
            "constant declarations did not cross semantic resolution")
    if gen.collected_aliases != gen.resolved_aliases:
        raise RuntimeError(
            "alias declarations did not cross semantic resolution")
    if gen.collected_enums != gen.resolved_enums:
        raise RuntimeError(
            "enum declarations did not cross semantic resolution")
    if gen.collected_extensions != gen.resolved_extensions:
        raise RuntimeError(
            "extension declarations did not cross semantic resolution")
    if gen.resolved_extensions != gen.checked_extensions:
        raise RuntimeError(
            "extension declarations did not cross semantic checking")

    pending = (
        gen.pending_conformance
        or gen.resolved_conformance
        or gen.pending_functions
    )
    if pending:
        raise RuntimeError(
            "semantic work remains at the LLVM lowering boundary")

    for name, info in gen.structs.items():
        if info.fields is not None or info.backing is not None:
            type_layout(gen, name)

    if (gen.module.globals
            or gen.module.context.identified_types
            or any(info.type is not None for info in gen.structs.values())):
        raise RuntimeError(
            "LLVM IR was constructed before semantic checking completed")

    gen.semantic_complete = True
