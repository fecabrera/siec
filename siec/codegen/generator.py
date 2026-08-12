"""Code generation state and entry point."""

import copy
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache

from llvmlite import ir

from siec.ast import Field, Function, Program
from siec.codegen.state import (
    CONTEXT_FIELDS,
    EmissionContext,
    FlowContext,
    GenericRegistry,
    SemanticModel,
    SourceContext,
    SymbolTable,
    TypeRegistry,
)


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
    # a closure may replace the original stack slot with stable heap storage;
    # every scope entry sharing this Variable then observes the promoted slot
    capture_promoted: bool = False


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
    Compilation state for one module, composed of phase-scoped contexts.

    Registries live on ``source``, ``symbols``, ``types``, ``generics``,
    ``flow``, and ``emission``. Attribute access still forwards to those
    containers so existing call sites keep working while phases migrate to
    the narrower interfaces.
    """

    def __init__(self, module_name: str, target: str | None = None):
        """
        Create an empty LLVM module to generate code into, aimed at the
        given target triple; the host's when none is given.
        """
        object.__setattr__(self, "source", SourceContext())
        object.__setattr__(self, "symbols", SymbolTable())
        object.__setattr__(self, "types", TypeRegistry())
        object.__setattr__(self, "generics", GenericRegistry())
        object.__setattr__(self, "flow", FlowContext())
        object.__setattr__(
            self, "emission", EmissionContext.create(module_name, target))
        # Filled by complete_semantics(); tooling should prefer reading it.
        object.__setattr__(self, "semantic", None)

    def __getattr__(self, name: str):
        owner = CONTEXT_FIELDS.get(name)
        if owner is not None:
            return getattr(object.__getattribute__(self, owner), name)
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}")

    def __setattr__(self, name: str, value) -> None:
        owner = CONTEXT_FIELDS.get(name)
        if owner is not None:
            setattr(object.__getattribute__(self, owner), name, value)
            return
        object.__setattr__(self, name, value)

    @contextmanager
    def in_file(self, path: str | None):
        """Resolve names under ``path`` for the duration of the block."""
        with self.source.in_file(path):
            yield

    @contextmanager
    def ungated(self):
        """Bypass file visibility for compiler-stamped type names."""
        with self.source.ungated():
            yield

    def resolve_symbol(self, name: str) -> str:
        """
        Resolve a Sie name to its module symbol: the current file's static
        when it has one, its member imports next, an '@symbol' mapping
        after, the public name otherwise.
        """
        return self.resolve_call_target(name)[0]

    def resolve_call_target(self, name: str) -> tuple[str, str | None]:
        """
        Resolve a call name to '(symbol, module file)'. The module is set
        for a member import or qualified 'module.f', so overload picking
        stays inside that module.
        """
        if (key := (self.current_file, name)) in self.statics:
            return self.statics[key], self.current_file

        member = self.member_targets.get((self.current_file, name))
        if member is not None:
            target, original = member
            original = self.module_type_symbols.get(
                (target, original), original)
            symbol = self.symbol_names.get(original, original)
            if symbol != original:
                origin = self.symbol_files.get(original)
                if origin not in self.include_closure.get(target, {target}):
                    symbol = original
            return symbol, target

        name = self.local_type_symbols.get((self.current_file, name), name)
        return self.symbol_names.get(name, name), None

    def resolve_type_symbol(self, name: str) -> str:
        """Resolve an unqualified type through its source module's view."""
        member = self.member_targets.get((self.current_file, name))
        if member is not None:
            target, original = member
            return self.module_type_symbols.get(
                (target, original), original)

        return self.local_type_symbols.get((self.current_file, name), name)

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
            member = self.module_type_symbols.get((target, member), member)
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

    def method_struct_type(self) -> str | None:
        """
        The struct type whose methods may access its private fields, or
        None when not checking a method body.
        """
        fn = self.checking_function
        if fn is None:
            return None

        if fn.receiver is not None:
            if fn.receiver_params:
                return f"{fn.receiver}<{','.join(fn.receiver_params)}>"
            return fn.receiver

        if "::" in fn.name:
            return fn.name.partition("::")[0]

        return None

    def can_access_private_field(self, struct_type: str) -> bool:
        """
        Whether the current checking context may read or write a private
        field of the given struct type.
        """
        from siec.codegen.aliases import expand_alias
        from siec.codegen.generics import split_generic
        from siec.codegen.types import strip_const, strip_reference

        method_type = self.method_struct_type()
        if method_type is None:
            return False

        struct_type = strip_const(strip_reference(expand_alias(self, struct_type)))
        method_type = strip_const(strip_reference(expand_alias(self, method_type)))

        if struct_type == method_type:
            return True

        struct_parts = split_generic(struct_type)
        method_parts = split_generic(method_type)
        if (struct_parts and method_parts
                and struct_parts[0] == method_parts[0]
                and len(struct_parts[1]) == len(method_parts[1])):
            return True

        fn = self.checking_function
        if fn is not None and fn.receiver is not None and fn.receiver_params:
            if struct_parts and struct_parts[0] == fn.receiver:
                return len(struct_parts[1]) == len(fn.receiver_params)
            if struct_type == fn.receiver:
                return True

        return False

    def resolve_callee(self, name: str) -> tuple[str | None, str | None]:
        """
        Resolve a call's name to '(symbol, module file)': dotted names
        through the module bindings, plain ones like any other symbol.
        """
        if "." in name:
            found = self.resolve_member(name.split("."))
            return (None, None) if found is None else found

        return self.resolve_call_target(name)

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

// a sealed marker implemented by the compiler's integer primitive types;
// unlike an ordinary interface, user declarations cannot claim it
interface Integer;

// sealed markers for the signed and unsigned integer primitives;
// unlike an ordinary interface, user declarations cannot claim them
interface SignedInteger;
interface UnsignedInteger;

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
    gen.import_targets = program.import_targets
    gen.module_exports = program.module_exports
    gen.local_type_symbols = program.local_type_symbols
    gen.module_type_symbols = program.module_type_symbols
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
    gen.builtin_names.update(("Result", "Ok", "Error", "Scalar", "Integer",
                              "SignedInteger", "UnsignedInteger", "Clone",
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
    gen.semantic = SemanticModel.snapshot(gen)