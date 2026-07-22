"""Declaration and emission of functions."""

from llvmlite import ir

from siec.ast import Function
from siec.codegen.abi import DIRECT, classify
from siec.codegen.aliases import expand_alias
from siec.codegen.asm import emit_asm_function
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator, Variable, make_volatile
from siec.codegen.overloads import declare_overload, overload_symbol
from siec.codegen.statements import emit_block
from siec.codegen.types import is_reference, resolve_type, strip_const


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
            return declare_function_body(gen, fn)
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


def declare_function_body(gen: CodeGenerator, fn: Function) -> ir.Function:
    """
    Build the function's declaration from its annotated Sie signature.

    'main' is special: it's always i32 to the C runtime, returning 0 when
    declared with no return type, and its 'args: char*[]' form keeps the
    C-level (i32, char**) signature underneath.
    """
    # a method declared through an alias ('String::init' for a
    # 'List<char>' alias) joins its canonical receiver's name, so its
    # overloads share one set with the struct's own methods
    if fn.receiver is not None and "::" in fn.name:
        canonical = strip_const(expand_alias(gen, fn.receiver))
        if canonical != fn.receiver:
            fn.name = f"{canonical}::{fn.name.partition('::')[2]}"
            fn.receiver = canonical

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

    # an '@symbol' function lives under its chosen module symbol, its Sie
    # name resolving there from everywhere
    symbol = fn.name
    if fn.symbol is not None:
        if fn.name == "main":
            raise TypeError("'main' cannot be renamed: the C runtime must find it")

        if gen.symbol_names.get(fn.name, fn.symbol) != fn.symbol:
            raise TypeError(f"conflicting '@symbol' names for function {fn.name!r}")

        gen.symbol_names[fn.name] = symbol = fn.symbol

    # a '@static' function is local to its file: it lives under a mangled
    # module symbol its own file resolves to, so other files neither see it
    # nor collide with its name
    if fn.is_static:
        if fn.name == "main":
            raise TypeError("'main' cannot be static: the C runtime must find it")

        key = (fn.file, fn.name)
        if key not in gen.statics:
            gen.statics[key] = f"{fn.name}.static.{len(gen.statics)}"

        symbol = gen.statics[key]

    # a second function under one name with a different parameter list is
    # an overload: it lives under a mangled sibling symbol, and calls pick
    # among the name's set by their argument types
    symbol = declare_overload(gen, fn, symbol)

    gen.return_types[symbol] = fn.return_type
    gen.param_types[symbol] = [p.type for p in fn.params]

    # defaults fill omitted call arguments; they emit under the
    # declaring file's view, so it travels with them
    if any(p.default is not None for p in fn.params):
        gen.param_defaults[symbol] = ([p.default for p in fn.params], fn.file)

    # redeclarations are allowed as long as the signature matches
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

    # an '@inline' function inlines into every caller, unconditionally
    if fn.is_inline:
        func.attributes.add("alwaysinline")

    # an '@noreturn' function never gives control back: calls to it end
    # their paths, and its own body must not return
    if fn.noreturn:
        func.attributes.add("noreturn")
        gen.noreturns.add(symbol)

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


def emit_function(gen: CodeGenerator, fn: Function) -> None:
    """
    Emit the body of a defined function into its declaration, tagging errors with its line.

    A nested statement tags its own line first, so the function line only fills
    in for errors raised outside any statement (a missing return, say).
    """
    with source_location(line=fn.line, file=fn.file):
        # the emitting file decides which statics its body's names resolve to
        gen.current_file = fn.file

        # a declaration that already has blocks was defined elsewhere; an
        # overloaded name's body belongs to its own signature's sibling
        symbol = overload_symbol(gen, gen.resolve_symbol(fn.name), fn.params)
        func = gen.module.globals[symbol]
        if func.blocks:
            raise TypeError(f"function {fn.name!r} is defined more than once")

        ret_type = func.function_type.return_type
        builder = ir.IRBuilder(func.append_basic_block("entry"))

        # under '-g', the function opens its debug scope, and every
        # instruction carries a location from here on
        if gen.debug is not None:
            gen.debug.enter_function(fn, func)
            builder.debug_metadata = gen.debug.location(fn.line)

        # an '@asm' function's parameters feed its assembly directly
        if fn.asm is not None:
            emit_asm_function(gen, builder, fn, func)
            return

        # the scope maps each name to a typed stack slot; spill the parameters into theirs
        scope = {}
        if main_takes_args(fn):
            spill_main_args(gen, builder, fn, func, scope)
        else:
            for arg, param in zip(func.args, fn.params):
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

                scope[param.name] = Variable(slot, param.type)
                store = builder.store(arg, slot)
                if gen.volatile_struct(arg.type):
                    make_volatile(store)

        # describe each parameter's slot to the debugger; a '&T' reference
        # arrives as a raw pointer argument, which dbg.declare cannot
        # describe, so a debug-only spill gives it addressable storage,
        # typed as the reference it is
        if gen.debug is not None:
            for position, param in enumerate(fn.params, 1):
                slot = scope[param.name].slot
                if is_reference(param.type):
                    shadow = builder.alloca(slot.type, name=f"{param.name}.ref")
                    builder.store(slot, shadow)
                    slot = shadow

                gen.debug.declare_variable(builder, slot, param.name,
                                           param.type, fn.line, arg=position)

        # emit the body statements starting from the entry block
        emit_block(gen, builder, fn.body, scope)

        # a void function may fall off the end, and so may main, whose
        # implicit exit code is 0; anything else must return
        if not builder.block.is_terminated:
            # an '@noreturn' body leaves through a noreturn call or loops
            # forever, so an open end block cannot actually be reached
            if fn.noreturn:
                builder.unreachable()
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
