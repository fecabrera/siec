"""Declaration and emission of functions."""

from llvmlite import ir

from siec.ast import Function
from siec.codegen.abi import DIRECT, classify
from siec.codegen.aliases import expand_alias
from siec.codegen.arity import CallArity
from siec.codegen.asm import emit_asm_function
from siec.codegen.errors import error_call_trace, source_location
from siec.codegen.generator import CodeGenerator, Variable, make_volatile
from siec.codegen.overloads import (
    declare_overload,
    overload_symbol,
    shown_signature,
)
from siec.codegen.statements import emit_block
from siec.codegen.types import (
    is_reference,
    resolve_type,
    strip_const,
    validate_type,
)


def declare_function(gen: CodeGenerator, fn: Function) -> ir.Function:
    """
    Declare a function in the module, reusing a matching earlier declaration.

    The declaring file's view resolves the signature's type names; the
    file is restored after, as instantiations declare mid-emission.
    """
    with source_location(line=fn.line, file=fn.file):
        previous = gen.current_file
        gen.current_file = fn.file
        try:
            resolve_function_body(gen, fn)
            return lower_function_body(gen, fn)
        finally:
            gen.current_file = previous


def resolve_function(gen: CodeGenerator, fn: Function) -> str:
    """Resolve and register a callable header without constructing LLVM IR."""
    with source_location(line=fn.line, file=fn.file):
        previous = gen.current_file
        gen.current_file = fn.file
        try:
            return resolve_function_body(gen, fn)
        finally:
            gen.current_file = previous


def main_takes_args(fn: Function) -> bool:
    """
    Whether this is the 'fn main(args: char*[])' entry form, whose single
    parameter lowers to the C-level argc/argv pair; a 'const' marking
    keeps the form.
    """
    return (fn.name == "main" and len(fn.params) == 1
            and strip_const(fn.params[0].type) == "char*[]")


def validate_main(fn: Function) -> None:
    """Validate the source-level entry point before lowering its native ABI."""
    if fn.name != "main":
        return

    if fn.is_extern:
        raise TypeError("'main' cannot be '@extern': the program must define "
                        "its entry point")
    if fn.is_inline:
        raise TypeError("'main' cannot be '@inline': the C runtime must call "
                        "its external definition")
    if fn.is_private:
        raise TypeError("'main' cannot be '@private': the program entry point "
                        "must be public")
    if fn.is_override:
        raise TypeError("'main' cannot be '@override': the program has one "
                        "fixed entry point")
    if fn.removed is not None:
        raise TypeError("'main' cannot be '@remove': the program must define "
                        "its entry point")

    valid_return = (fn.return_type is None
                    or strip_const(fn.return_type) == "i32")
    params = tuple(strip_const(param.type) for param in fn.params)
    valid_params = params in ((), ("i32", "char**"), ("char*[]",))
    has_defaults = any(param.default is not None for param in fn.params)

    if (not valid_return or not valid_params or fn.var_arg or fn.variadic
            or has_defaults):
        raise TypeError(
            "'main' must have one of these signatures: fn main(), "
            "fn main(i32, char**), or fn main(char*[]), optionally "
            "returning i32")


def join_canonical_receiver(gen: CodeGenerator, fn) -> None:
    """
    A method declared through an alias ('String::init' for a 'List<char>'
    alias) joins its canonical receiver's name, so its overloads share
    one set with the struct's own methods.
    """
    if fn.receiver is None or "::" not in fn.name:
        return

    canonical = strip_const(expand_alias(gen, fn.receiver))
    if canonical != fn.receiver:
        fn.name = f"{canonical}::{fn.name.partition('::')[2]}"
        fn.receiver = canonical


def resolve_function_body(gen: CodeGenerator, fn: Function) -> str:
    """
    Resolve a function's annotated Sie signature into the callable inventory.
    """
    join_canonical_receiver(gen, fn)
    if fn.receiver is not None and fn.is_private:
        gen.private_methods.setdefault(fn.name, set()).add(fn.file)

    fn.return_type = expand_alias(gen, fn.return_type)
    for param in fn.params:
        param.type = expand_alias(gen, param.type)

    # a returned reference must alias storage that outlives the call: it
    # can only derive from a reference parameter, the receiver usually
    if is_reference(fn.return_type):
        if fn.is_extern:
            raise TypeError("an '@extern' function cannot return a reference")

        first = fn.params[0].type if fn.params else None
        if not is_reference(strip_const(first)):
            raise TypeError("a reference return must derive from a reference "
                            "parameter: the value must outlive the call")

    validate_type(fn.return_type, gen.structs)
    for param in fn.params:
        validate_type(param.type, gen.structs)

    # an '@symbol' function lives under its chosen module symbol, its Sie
    # name resolving there from everywhere
    symbol = fn.name
    if fn.symbol is not None:
        if fn.name == "main":
            raise TypeError("'main' cannot be renamed: the C runtime must find it")

        if gen.symbol_names.get(fn.name, fn.symbol) != fn.symbol:
            raise TypeError(f"conflicting '@symbol' names for function {fn.name!r}")

        gen.symbol_names[fn.name] = symbol = fn.symbol
        gen.symbol_files[fn.name] = fn.file

    # a '@static' function is local to its file: it lives under a mangled
    # module symbol its own file resolves to, so other files neither see it
    # nor collide with its name; an already-pinned symbol (a template's
    # instance, mangled at instantiation) keeps its name
    if fn.is_static and fn.symbol is None:
        if fn.name == "main":
            raise TypeError("'main' cannot be static: the C runtime must find it")

        key = (fn.file, fn.name)
        if key not in gen.statics:
            gen.statics[key] = f"{fn.name}.static.{len(gen.statics)}"

        symbol = gen.statics[key]

    validate_main(fn)

    # one name cannot be both a function and a global, whatever symbol
    # the function's signature mangles to below
    if symbol in gen.globals:
        raise TypeError(f"{fn.name!r} is declared as both a function and a global")

    # a second function under one name with a different parameter list is
    # an overload: it lives under a mangled sibling symbol, and calls pick
    # among the name's set by their argument types
    symbol = declare_overload(gen, fn, symbol)

    gen.return_types[symbol] = fn.return_type
    gen.param_types[symbol] = [p.type for p in fn.params]
    gen.call_arities[symbol] = CallArity.from_parameters(
        fn.params, variadic=fn.variadic, var_arg=fn.var_arg)

    # '@deprecated' advice travels with the symbol: its reachable uses
    # warn once the program is emitted, and a '@remove' one's fail
    if fn.deprecated is not None:
        gen.deprecated[symbol] = fn.deprecated

    if fn.removed is not None:
        gen.removed[symbol] = fn.removed

    # defaults fill omitted call arguments; they emit under the
    # declaring file's view, so it travels with them
    if any(p.default is not None for p in fn.params):
        gen.param_defaults[symbol] = ([p.default for p in fn.params], fn.file)

    # an 'args...' function packs its calls' extra arguments
    if fn.variadic:
        gen.variadics.add(symbol)
    if fn.var_arg:
        gen.var_args.add(symbol)

    if fn.noreturn:
        gen.noreturns.add(symbol)

    signature = (
        fn.return_type,
        tuple(param.type for param in fn.params),
        fn.var_arg,
        fn.is_extern,
        main_takes_args(fn),
        fn.returns_self,
    )
    existing = gen.function_signatures.get(symbol)
    if existing is not None and existing != signature:
        raise TypeError(f"conflicting declarations for function {fn.name!r}")

    gen.function_signatures[symbol] = signature
    if fn.returns_self:
        gen.self_returns.add(symbol)
    gen.resolved_functions.setdefault(symbol, fn)
    return symbol


def lower_function_body(gen: CodeGenerator, fn: Function,
                        symbol: str | None = None) -> ir.Function:
    """Lower one resolved callable header into an LLVM declaration."""
    if symbol is None:
        symbol = overload_symbol(gen, gen.resolve_symbol(fn.name), fn.params)

    if fn.name == "main" and fn.return_type is None:
        ret_type = ir.IntType(32)
    else:
        ret_type = resolve_type(fn.return_type, gen.structs)

    if main_takes_args(fn):
        param_types = [ir.IntType(32), resolve_type("char**", gen.structs)]
    else:
        param_types = [resolve_type(p.type, gen.structs) for p in fn.params]

    # an '@extern' function's struct parameters travel the C ABI: small
    # ones reshaped into register values, large ones through memory
    lowerings = None
    if fn.is_extern:
        lowerings = [
            classify(gen, type_, info.is_union)
            if (info := gen.structs.get(strip_const(param.type))) is not None
            and info.fields is not None else DIRECT
            for param, type_ in zip(fn.params, param_types)]

        if all(lowering == DIRECT for lowering in lowerings):
            lowerings = None
        else:
            param_types = [
                type_ if kind == "direct"
                else coerce if kind == "coerce" else ir.PointerType(type_)
                for type_, (kind, coerce) in zip(param_types, lowerings)]

    # a struct return comes back the C way too: reshaped into registers,
    # or written through a hidden first 'sret' pointer
    ret_lowering = None
    if fn.is_extern and (
            (info := gen.structs.get(strip_const(fn.return_type))) is not None
            and info.fields is not None):
        kind, coerce = classify(gen, ret_type, info.is_union)
        if kind == "coerce":
            ret_lowering = ("coerce", coerce, ret_type)
            ret_type = coerce
        elif kind == "indirect":
            ret_lowering = ("indirect", None, ret_type)
            param_types = [ir.PointerType(ret_type), *param_types]
            ret_type = ir.VoidType()

    func_type = ir.FunctionType(ret_type, param_types, var_arg=fn.var_arg)
    existing = gen.module.globals.get(symbol)
    if existing is not None:
        if not isinstance(existing, ir.Function):
            raise TypeError(f"{fn.name!r} is declared as both a function and a global")
        if existing.function_type != func_type:
            raise TypeError(f"conflicting declarations for function {fn.name!r}")
        func = existing
    else:
        func = ir.Function(gen.module, func_type, name=symbol)

    if fn.is_static:
        func.linkage = "internal"

    if fn.is_inline:
        func.attributes.add("alwaysinline")
        if gen.unit_files is not None and not fn.is_static:
            func.linkage = "linkonce_odr"

    if fn.noreturn:
        func.attributes.add("noreturn")

    # record the ABI lowerings for calls to mirror; x86-64's large
    # aggregates carry 'byval', copying onto the stack at the call, and an
    # indirect return marks its hidden pointer 'sret'
    hidden = 0
    if ret_lowering is not None:
        gen.abi_returns[symbol] = ret_lowering

        if ret_lowering[0] == "indirect":
            hidden = 1
            func.args[0].add_attribute("sret")

    if lowerings is not None:
        gen.abi_args[symbol] = [None if low == DIRECT else low
                                for low in lowerings]

        for arg, lowering in zip(func.args[hidden:], lowerings):
            if lowering == ("indirect", True):
                arg.add_attribute("byval")

    return func


def lower_functions(gen: CodeGenerator) -> None:
    """Lower every resolved callable header after semantic checking."""
    for symbol, fn in gen.resolved_functions.items():
        lower_function_body(gen, fn, symbol)


def link_once(gen: CodeGenerator, fn: Function) -> None:
    """
    Mark an instantiated function's definition 'linkonce_odr': under
    separate compilation, every unit stamps the instances its own calls
    use, so identical definitions from other units merge at link.
    """
    gen.current_file = fn.file
    symbol = overload_symbol(gen, gen.resolve_symbol(fn.name), fn.params)
    func = gen.module.globals[symbol]

    if func.linkage != "internal":
        func.linkage = "linkonce_odr"


def emit_function(gen: CodeGenerator, fn: Function) -> None:
    """
    Emit the body of a defined function into its declaration, tagging errors with its line.

    A nested statement tags its own line first, so the function line only fills
    in for errors raised outside any statement (a missing return, say).
    """
    with source_location(line=fn.line, file=fn.file), error_call_trace(gen):
        # the emitting file decides which statics its body's names resolve to
        gen.current_file = fn.file

        # a declaration that already has blocks was defined elsewhere; an
        # overloaded name's body belongs to its own signature's sibling
        symbol = overload_symbol(gen, gen.resolve_symbol(fn.name), fn.params)
        gen.current_function = symbol
        gen.current_line = fn.line
        if gen.emitting and symbol not in gen.checked_functions:
            raise RuntimeError(
                f"LLVM emission received unchecked function {symbol!r}")
        func = gen.module.globals[symbol]
        if func.blocks:
            raise TypeError(f"function '{shown_signature(fn)}' "
                            "is defined more than once")

        # the names this body uses are recorded against it: the call graph
        # the deprecation walk reads
        gen.call_graph.setdefault(symbol, set())

        ret_type = func.function_type.return_type
        builder = ir.IRBuilder(func.append_basic_block("entry"))

        # under '-g', the function opens its debug scope, and every
        # instruction carries a location from here on
        if gen.debug is not None:
            gen.debug.enter_function(fn, func)
            builder.debug_metadata = gen.debug.location(fn.line)

        from siec.codegen.slots import emit_slot_function

        if emit_slot_function(gen, builder, fn, func):
            return

        # an '@asm' function's parameters feed its assembly directly
        if fn.asm is not None:
            emit_asm_function(gen, builder, fn, func)
            return

        # the scope maps each name to a typed stack slot; spill the parameters into theirs
        scope = {}
        parameter_cleanups = []
        if main_takes_args(fn):
            spill_main_args(gen, builder, fn, func, scope)
        else:
            for position, (arg, param) in enumerate(
                    zip(func.args, fn.params)):
                arg.name = param.name

                # a reference parameter's slot IS the caller's address:
                # reads and writes go through it, aliasing the argument
                if is_reference(param.type):
                    scope[param.name] = Variable(arg, param.type)
                    continue

                slot = builder.alloca(arg.type, name=f"{param.name}.addr")

                # an '@align(N)' struct's slot honors the declared alignment
                if (align := gen.struct_align(param.type)) is not None:
                    slot.align = align

                from siec.codegen.ownership import (DropCleanup,
                                                   assign_adopts_parameter,
                                                   destroyable,
                                                   new_drop_flag)

                owned = (destroyable(gen, param.type)
                         and not assign_adopts_parameter(
                             gen, fn, position))
                drop_flag = (new_drop_flag(builder, param.name, True)
                             if owned else None)
                scope[param.name] = Variable(
                    slot, param.type, drop_flag=drop_flag)
                if owned:
                    parameter_cleanups.append(
                        DropCleanup(param.name, scope[param.name]))
                store = builder.store(arg, slot)
                if gen.volatile_struct(arg.type):
                    make_volatile(store)

                if param.pattern is not None:
                    from siec.codegen.statements import bind_tuple_value

                    bind_tuple_value(
                        gen, builder, param.pattern, param.pattern_types,
                        arg, scope,
                        line=fn.line)

        # describe each parameter's slot to the debugger; a '&T' reference
        # arrives as a raw pointer argument, which dbg.declare cannot
        # describe, so a debug-only spill gives it addressable storage,
        # typed as the reference it is. Patterned params declare their
        # element bindings instead of the synthetic tuple name.
        if gen.debug is not None:
            for position, param in enumerate(fn.params, 1):
                if param.pattern is not None:
                    continue

                slot = scope[param.name].slot
                if is_reference(param.type):
                    shadow = builder.alloca(slot.type, name=f"{param.name}.ref")
                    builder.store(slot, shadow)
                    slot = shadow

                gen.debug.declare_variable(builder, slot, param.name,
                                           param.type, fn.line, arg=position)

        # emit the body statements starting from the entry block
        emit_block(
            gen, builder, fn.body, scope,
            initial_cleanups=parameter_cleanups)

        # a void function may fall off the end, and so may main, whose
        # implicit exit code is 0; anything else must return
        if not builder.block.is_terminated:
            # an '@noreturn' body leaves through a noreturn call or loops
            # forever, so an open end block cannot actually be reached
            if fn.noreturn:
                builder.unreachable()
            elif fn.returns_self:
                builder.ret(scope["self"].slot)
            elif isinstance(ret_type, ir.VoidType):
                builder.ret_void()
            elif fn.name == "main" and fn.return_type is None:
                builder.ret(ir.Constant(ret_type, 0))
            else:
                raise TypeError(f"function {fn.name!r} must return a value")


def spill_main_args(gen: CodeGenerator, builder: ir.IRBuilder, fn: Function,
                    func: ir.Function, scope: dict) -> None:
    """
    Spill the 'args: char*[]' entry form: wrap the C-level argc/argv
    arguments into the fat array the parameter declares.
    """
    argc, argv = func.args
    argc.name, argv.name = "argc", "argv"

    # 'let args: char*[] = {argv, argc as u64};', done for the body
    args_type = resolve_type("char*[]", gen.structs)
    value = ir.Constant(args_type, ir.Undefined)
    value = builder.insert_value(value, argv, 0)
    value = builder.insert_value(value, builder.zext(argc, ir.IntType(64)), 1)

    param = fn.params[0]
    slot = builder.alloca(args_type, name=f"{param.name}.addr")
    builder.store(value, slot)
    scope[param.name] = Variable(slot, param.type)
