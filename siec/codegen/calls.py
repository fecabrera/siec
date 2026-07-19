"""Emission of function calls: direct, indirect, and their arguments."""

from llvmlite import ir

from siec.ast import Call, Expr
from siec.codegen.abi import lift_return, lower_argument
from siec.codegen.coercion import emit_coerced
from siec.codegen.generator import CodeGenerator, entry_alloca
from siec.codegen.inference import expr_sie_type
from siec.codegen.types import (
    fn_type_parts,
    is_const,
    is_reference,
    strip_const,
    strip_reference,
)


def emit_call(gen: CodeGenerator, builder: ir.IRBuilder, call: Call, scope: dict):
    """
    Emit a call to a declared function, checking the argument count.
    """
    # deferred import: calls and expressions are mutually recursive
    from siec.codegen.expressions import emit_expression

    # a dotted name resolves through the file's module bindings
    if "." in call.name:
        symbol = gen.resolve_qualified(call.name.split("."))
        if symbol is None:
            raise NameError(f"undefined function {call.name!r}")

        if symbol in gen.globals:
            return emit_indirect_call(gen, builder, call, scope, symbol)
    else:
        # a name in scope is always in view; anything else must be visible
        # to this file: an imported module's names need their qualified
        # spelling or a member import
        if call.name not in scope and not gen.sees(call.name):
            raise NameError(f"undefined function {call.name!r}")

        # a variable or global holding a function reference is called through
        # its value; the current file's statics resolve first, other files' never
        symbol = gen.resolve_symbol(call.name)
        if call.name in scope or symbol in gen.globals:
            return emit_indirect_call(gen, builder, call, scope)

    # look up the callee among the module's declared functions
    func = gen.module.globals.get(symbol)
    if not isinstance(func, ir.Function):
        raise NameError(f"undefined function {call.name!r}")

    # check arity, letting varargs functions take extra arguments; an
    # indirect struct return hides its own first parameter
    ret_lowering = gen.abi_returns.get(func.name)
    hidden = 1 if ret_lowering is not None and ret_lowering[0] == "indirect" else 0
    expected = len(func.function_type.args) - hidden

    if len(call.args) < expected:
        raise TypeError(f"too few arguments to function {call.name!r}")

    if len(call.args) > expected and not func.function_type.var_arg:
        raise TypeError(f"too many arguments to function {call.name!r}")

    # coerce each argument to its parameter's Sie type; vararg extras pass
    # as-is, except an f32, which promotes to f64 like C's default promotions
    sie_params = gen.param_types.get(func.name, [])

    args = []
    for i, arg in enumerate(call.args):
        if i < len(sie_params):
            args.append(emit_argument(gen, builder, arg, sie_params[i], scope))
        else:
            value = emit_expression(gen, builder, arg, None, scope)
            if isinstance(value.type, ir.FloatType):
                value = builder.fpext(value, ir.DoubleType())

            args.append(value)

    # an '@extern' callee's struct arguments reshape for the C ABI
    lowerings = gen.abi_args.get(func.name)
    if lowerings is not None:
        for i, lowering in enumerate(lowerings):
            if lowering is not None and i < len(args):
                args[i] = lower_argument(gen, builder, args[i], lowering)

    # and its struct return comes back through registers or the hidden slot
    if ret_lowering is not None:
        kind, _, struct_type = ret_lowering
        if kind == "indirect":
            out = entry_alloca(builder, struct_type, "sret.out")
            builder.call(func, [out, *args])
            return builder.load(out)

        return lift_return(gen, builder, builder.call(func, args), struct_type)

    return builder.call(func, args)


def emit_argument(gen: CodeGenerator, builder: ir.IRBuilder, arg: Expr,
                  param_name: str, scope: dict):
    """
    Emit one call argument: coerced to the parameter's type, or, for a '&T'
    reference parameter, the argument's own address, passed implicitly.
    """
    # deferred import: calls and expressions are mutually recursive
    from siec.codegen.expressions import emit_lvalue

    if not is_reference(param_name):
        return emit_coerced(gen, builder, arg, param_name, scope)

    referenced = strip_reference(param_name)
    arg_name = expr_sie_type(gen, arg, scope)

    if arg_name is not None:
        # the callee aliases the storage itself, so the types must match
        # exactly: no widening can happen in place
        if strip_const(arg_name) != strip_const(referenced):
            raise TypeError(f"cannot bind a {arg_name!r} value to a "
                            f"{param_name!r} parameter")

        # a const value only binds to a 'const &T'
        if is_const(arg_name) and not is_const(referenced):
            raise TypeError(f"cannot bind a {arg_name!r} value to a mutable "
                            f"{param_name!r} parameter")

    try:
        return emit_lvalue(gen, builder, arg, scope)
    except TypeError:
        raise TypeError(f"a {param_name!r} parameter needs an "
                        "assignable argument") from None


def emit_indirect_call(gen: CodeGenerator, builder: ir.IRBuilder, call: Call,
                       scope: dict, symbol: str | None = None):
    """
    Emit a call through a function reference held in a variable or a global,
    the latter under an already-resolved symbol when one is given.
    """
    if symbol is not None:
        var_type, slot = strip_const(gen.globals[symbol]), gen.module.globals[symbol]
    elif call.name in scope:
        var = scope[call.name]
        var_type, slot = strip_const(var.type), var.slot
    else:
        symbol = gen.resolve_symbol(call.name)
        var_type, slot = strip_const(gen.globals[symbol]), gen.module.globals[symbol]

    if not var_type.startswith("fn(") or fn_type_parts(var_type)[2]:
        raise TypeError(f"cannot call non-function variable {call.name!r}")

    sie_params = fn_type_parts(var_type)[0]
    if len(call.args) != len(sie_params):
        raise TypeError(f"function reference {call.name!r} takes "
                        f"{len(sie_params)} arguments, got {len(call.args)}")

    callee = builder.load(slot, name=call.name)
    args = [emit_argument(gen, builder, arg, sie_params[i], scope)
            for i, arg in enumerate(call.args)]

    return builder.call(callee, args)
