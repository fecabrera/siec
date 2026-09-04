"""Emission of function calls: direct, indirect, and their arguments."""

from llvmlite import ir

from siec.ast import Call, Expr, Var
from siec.codegen.abi import lift_return, lower_argument
from siec.codegen.coercion import emit_coerced
from siec.codegen.generator import CodeGenerator, entry_alloca
from siec.codegen.inference import expr_sie_type
from siec.codegen.types import (
    fn_type_parts,
    is_const,
    is_reference,
    resolve_type,
    strip_const,
    strip_reference,
)


def emit_call(gen: CodeGenerator, builder: ir.IRBuilder, call: Call, scope: dict,
              as_address: bool = False):
    """
    Emit a call from the target and receiver action selected during Check.

    A '&T'-returning callee yields the T's address: reading the call
    loads through it, while 'as_address' keeps the address itself, for
    lvalue use - member assignment, or a method's receiver.
    """
    # deferred import: calls and expressions are mutually recursive
    from siec.codegen.expressions import emit_expression
    from siec.codegen.hir import checked_call

    # a macro call expands in place instead of resolving a function; in
    # an untyped context its 'emit' value types the block
    from siec.codegen.macros import macro_expansion_view

    with macro_expansion_view(gen, call, scope) as macro:
        if macro is not None:
            expansion = macro.expansion
            if macro.kind == "block":
                raise TypeError(
                    f"macro {macro.name!r} does not 'emit' a value")

            from siec.codegen.expressions import emit_block_expr, emit_expression
            from siec.codegen.inference import infer_type

            if macro.kind == "block_expression":
                emitted = (getattr(call, "sie_type", None)
                           or infer_type(gen, call, scope))
                target = (resolve_type(emitted, gen.structs)
                          if emitted is not None else None)
                return emit_block_expr(gen, builder, expansion, target, scope,
                                       emitted)

            # an expression macro substitutes its expression in place
            return emit_expression(gen, builder, expansion, None, scope)

    plan = checked_call(call)
    if plan is None:
        # Low-level emitter tests construct a generator without entering the
        # compiler's Emit phase. Keep their direct-call fixture useful while
        # the production pipeline requires Check to supply every plan.
        if not gen.emitting:
            from siec.codegen.hir import CallPlan

            symbol, _ = gen.resolve_callee(call.name)
            if not isinstance(gen.module.globals.get(symbol), ir.Function):
                from siec.codegen.overloads import overload_candidates

                candidates = overload_candidates(gen, symbol)
                symbol = candidates[0] if candidates else symbol
            func = gen.module.globals.get(symbol)
            if not isinstance(func, ir.Function):
                raise NameError(f"undefined function {call.name!r}")
            count_error = gen.call_arities[symbol].error(len(call.args))
            if count_error is not None:
                raise TypeError(
                    f"{count_error} arguments to function {call.name!r}")
            plan = CallPlan("direct", symbol=symbol)
        else:
            raise RuntimeError(
                f"call {call.name!r} reached Emit without a checked call plan")

    if plan.kind == "rewrite":
        return emit_expression(gen, builder, plan.replacement, None, scope)

    if plan.kind == "indirect":
        return emit_indirect_call(
            gen,
            builder,
            call,
            scope,
            plan.indirect_type,
            plan.indirect_symbol,
        )

    if plan.kind == "constructor":
        from siec.codegen.methods import emit_constructor

        return emit_constructor(
            gen,
            builder,
            plan.constructor_type,
            plan.symbol,
            call,
            scope,
            as_address,
        )

    if plan.kind != "direct" or plan.symbol is None:
        raise RuntimeError(f"invalid checked call plan for {call.name!r}")

    if plan.receiver is not None:
        if plan.passes_receiver:
            written_call = call
            call = Call(
                call.name,
                [plan.receiver, *call.args],
                call.type_args,
            )
            if hasattr(written_call, "packed_variadic"):
                call.packed_variadic = written_call.packed_variadic
        else:
            emit_expression(gen, builder, plan.receiver, None, scope)

    symbol = plan.symbol
    from siec.codegen.deprecation import note_use
    from siec.codegen.worklist import activate_function_instance

    activate_function_instance(gen, symbol)
    note_use(gen, symbol)

    func = gen.module.globals.get(symbol)
    if not isinstance(func, ir.Function):
        raise RuntimeError(f"checked callee {symbol!r} was not lowered")

    # only a reference-returning call has an address to keep
    if as_address and not is_reference(gen.return_types.get(func.name)):
        raise TypeError("cannot take the address of a call's value")

    return _emit_resolved_call(gen, builder, call, scope, func, as_address)


def _emit_resolved_call(gen: CodeGenerator, builder: ir.IRBuilder, call: Call,
                        scope: dict, func: ir.Function, as_address: bool):
    """Emit arguments and the call once the concrete LLVM callee is known."""
    from siec.codegen.expressions import emit_expression

    # Source arity is already checked. Its parameter count still controls
    # variadic packing and default insertion during lowering.
    arity = gen.call_arities[func.name]
    expected = arity.parameter_count
    ret_lowering = gen.abi_returns.get(func.name)

    # a 'name...' variadic packs the call's extra arguments into its
    # trailing const Any[]; an explicit Any[] argument forwards as-is
    if func.name in gen.variadics:
        call = getattr(call, "packed_variadic", None) or pack_variadic(
            gen, call, expected, scope)

    # trailing parameters with defaults are optional at the call
    defaults, defaults_file = gen.param_defaults.get(func.name, ([], None))
    # coerce each argument to its parameter's Sie type; vararg extras pass
    # as-is, except an f32, which promotes to f64 like C's default promotions
    sie_params = gen.param_types.get(func.name, [])

    from siec.codegen.ownership import begin_temporary_frame

    owns_temporary_frame = begin_temporary_frame(gen)

    args = []
    try:
        for i, arg in enumerate(call.args):
            if i < len(sie_params):
                args.append(emit_argument(
                    gen, builder, arg, sie_params[i], scope))
            else:
                value = emit_expression(gen, builder, arg, None, scope)
                if isinstance(value.type, ir.FloatType):
                    value = builder.fpext(value, ir.DoubleType())

                args.append(value)
    except Exception:
        if owns_temporary_frame:
            gen.borrowed_temporary_frames.pop()
        raise

    # omitted arguments take their declared defaults, emitted under the
    # declaring file's view, away from any local names
    if len(call.args) < expected:
        previous, gen.current_file = gen.current_file, defaults_file
        try:
            for i in range(len(call.args), expected):
                args.append(emit_argument(gen, builder, defaults[i],
                                          sie_params[i], {}))
        finally:
            gen.current_file = previous

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
            result = builder.load(out)
            finish_borrowed_temporaries(
                gen, builder, owns_temporary_frame)
            return result

        result = lift_return(
            gen, builder, builder.call(func, args), struct_type)
        finish_borrowed_temporaries(gen, builder, owns_temporary_frame)
        return result

    result = builder.call(func, args)

    finish_borrowed_temporaries(gen, builder, owns_temporary_frame)

    # a reference return is the referenced value's address; reading the
    # call as a value loads through it
    if is_reference(gen.return_types.get(func.name)):
        return result if as_address else builder.load(result)

    return result


def finish_borrowed_temporaries(gen: CodeGenerator, builder,
                                owns_frame: bool) -> None:
    """Destroy borrowed rvalues after the outermost containing call."""
    from siec.codegen.ownership import finish_temporary_frame

    finish_temporary_frame(gen, builder, owns_frame)


def pack_variadic(gen: CodeGenerator, call: Call, expected: int,
                  scope: dict) -> Call:
    """
    Rewrite a call to an 'args...' function: the arguments past the
    fixed ones wrap as Anys and pack into one borrowed const Any[] view - an
    empty one when none are given. Passing an Any[] itself forwards it.
    """
    from siec.ast import ArrayLiteral, Cast
    from siec.codegen.inference import infer_type
    from siec.codegen.types import strip_reference

    fixed = expected - 1
    if len(call.args) < fixed:
        return call

    # exactly filled, the last already an Any[] view: a forward, not a pack
    if len(call.args) == expected:
        last = infer_type(gen, call.args[-1], scope)
        if last is not None and strip_const(strip_reference(last)) == "Any[]":
            return call

    extras = [Cast(arg, "Any") for arg in call.args[fixed:]]
    return Call(call.name, [*call.args[:fixed], ArrayLiteral(extras)],
                call.type_args)


def emit_argument(gen: CodeGenerator, builder: ir.IRBuilder, arg: Expr,
                  param_name: str, scope: dict):
    """
    Emit one call argument: coerced to the parameter's type, or, for a '&T'
    reference parameter, the argument's own address, passed implicitly.
    """
    # deferred import: calls and expressions are mutually recursive
    from siec.codegen.expressions import emit_expression, emit_lvalue

    if not is_reference(param_name):
        value = emit_coerced(gen, builder, arg, param_name, scope)
        from siec.codegen.ownership import (consume_temporary,
                                           disarm_expression)

        # A const by-value parameter receives a non-owning view. A temporary
        # remains the caller's responsibility and is destroyed after the
        # complete call; a named source remains armed in its own scope.
        if not is_const(param_name):
            consume_temporary(gen, arg)
            disarm_expression(gen, builder, arg, scope)
        return value

    referenced = strip_reference(param_name)
    arg_name = expr_sie_type(gen, arg, scope)

    if arg_name is not None:
        # A mutable reference aliases the caller's storage and therefore
        # needs an exact type. A const reference may point at a converted
        # spill, since the callee cannot observe that it is temporary.
        if strip_const(arg_name) != strip_const(referenced):
            from siec.codegen.overloads import parameter_fit

            if (not is_const(referenced)
                    or parameter_fit(
                        gen, arg, arg_name, strip_const(referenced)) is None):
                raise TypeError(f"cannot bind a {arg_name!r} value to a "
                                f"{param_name!r} parameter")
            value = emit_coerced(gen, builder, arg, referenced, scope)
            slot = entry_alloca(builder, value.type, "ref.spill")
            builder.store(value, slot)
            return slot

        # a const value only binds to a 'const &T'
        if is_const(arg_name) and not is_const(referenced):
            raise TypeError(f"cannot bind a {arg_name!r} value to a mutable "
                            f"{param_name!r} parameter")

    from siec.ast import Cast, Index, Member, MethodCall, UnaryOp
    from siec.codegen.inference import (enum_backing, numeric_class,
                                        sized_member_array, type_info)
    from siec.codegen.ownership import (TemporaryDrop, destroyable,
                                       expression_returns_reference)

    # A sized field owns inline backing rather than a stored slice descriptor.
    # A const reference may borrow a temporary descriptor whose data still
    # points at that backing; a mutable reference could rebind only the
    # temporary descriptor, so it is deliberately rejected.
    if sized_member_array(gen, arg, scope) is not None:
        if not is_const(referenced):
            raise TypeError("a sized array field can only bind to a const "
                            "array reference")
        value = emit_expression(gen, builder, arg, None, scope)
        slot = entry_alloca(builder, value.type, "sized.ref")
        builder.store(value, slot)
        return slot

    # A represented aggregate cast may deliberately reinterpret an existing
    # place (for example a layout-compatible view returned by reference).
    # Numeric and pointer casts instead produce values; a method receiver
    # materializes that converted value rather than retyping the operand's
    # storage. This is what makes ``(const_i32 as i64).method()`` a legal
    # call on a fresh i64 temporary.
    cast_place = (isinstance(arg, Cast)
                  and type_info(gen, arg.type) is not None
                  and numeric_class(enum_backing(gen, arg.type)) is None)
    addressable = (
        isinstance(arg, (Var, Member, Index))
        or cast_place
        or isinstance(arg, UnaryOp) and arg.op == "*"
        or isinstance(arg, (Call, MethodCall))
        and expression_returns_reference(gen, arg)
    )
    if addressable:
        return emit_lvalue(gen, builder, arg, scope)

    try:
        # a literal has no storage to alias, and no declared type to
        # spill at; against a 'const &T' it materializes at the
        # parameter's own type - a mutable reference stays an error,
        # its writes would land on the temporary
        if arg_name is None and not is_const(referenced):
            raise TypeError(f"a {param_name!r} parameter needs an "
                            "assignable argument") from None

        value = emit_coerced(gen, builder, arg, referenced, scope)
        from siec.codegen.ownership import (expression_identity,
                                           temporary_registered,
                                           temporary_slot)

        # A destroyable call result already occupies a unique slot. Alias
        # it rather than copying: a mutable reference may reallocate, and
        # Destroy must run against that same storage.
        if (existing := temporary_slot(gen, arg)) is not None:
            return existing

        slot = entry_alloca(builder, value.type, "ref.spill")
        builder.store(value, slot)
        if (destroyable(gen, referenced)
                and not temporary_registered(gen, arg)
                and gen.borrowed_temporary_frames):
            gen.borrowed_temporary_frames[-1].append(
                TemporaryDrop(slot, strip_const(referenced),
                              expression_identity(arg)))
        return slot
    except TypeError:
        raise


def emit_indirect_call(gen: CodeGenerator, builder: ir.IRBuilder, call: Call,
                       scope: dict, type_name: str,
                       symbol: str | None = None):
    """
    Emit a checked call through a local or global function reference.
    """
    var_type = strip_const(type_name)
    if symbol is not None:
        slot = gen.module.globals[symbol]
    else:
        slot = scope[call.name].slot

    closure = var_type.startswith("closure fn(")
    sie_params = fn_type_parts(var_type)[0]

    callee = builder.load(slot, name=call.name)
    if closure:
        from siec.codegen.closures import emit_closure_call

        return emit_closure_call(
            gen, builder, callee, var_type, call.args, scope)

    args = [emit_argument(gen, builder, arg, sie_params[i], scope)
            for i, arg in enumerate(call.args)]

    result = builder.call(callee, args)

    # a reference-returning callee yields the value's address; reading
    # the call as a value loads through it
    if is_reference(fn_type_parts(var_type)[1] or ""):
        return builder.load(result)

    return result
