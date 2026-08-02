"""Emission of function calls: direct, indirect, and their arguments."""

from llvmlite import ir

from siec.ast import Block, BlockExpr, Call, Expr, Var
from siec.codegen.abi import lift_return, lower_argument
from siec.codegen.coercion import emit_coerced
from siec.codegen.generator import CodeGenerator, entry_alloca
from siec.codegen.generics import instantiate_function, pick_generic_call
from siec.codegen.methods import method_call, qualified_method
from siec.codegen.inference import expr_sie_type
from siec.codegen.overloads import pick_overload
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
    Emit a call to a declared function, checking the argument count.

    A '&T'-returning callee yields the T's address: reading the call
    loads through it, while 'as_address' keeps the address itself, for
    lvalue use - member assignment, or a method's receiver.
    """
    # deferred import: calls and expressions are mutually recursive
    from siec.codegen.expressions import emit_expression

    # a typed context (a coercion target) may drive a generic callee's
    # type arguments; captured before any receiver rewrite drops it
    expected = getattr(call, "expected_type", None)

    # a macro call expands in place instead of resolving a function; in
    # an untyped context its 'emit' value types the block
    from siec.codegen.macros import resolve_macro_use

    if resolve_macro_use(gen, call, scope) is not None:
        from siec.codegen.expressions import emit_block_expr, emit_expression
        from siec.codegen.inference import infer_type
        from siec.codegen.macros import macro_expansion, macro_view

        expansion = macro_expansion(gen, call)
        if isinstance(expansion, Block):
            raise TypeError(f"macro {call.name!r} does not 'emit' a value")

        with macro_view(gen, call.name):
            if isinstance(expansion, BlockExpr):
                emitted = infer_type(gen, call, scope)
                target = (resolve_type(emitted, gen.structs)
                          if emitted is not None else None)
                return emit_block_expr(gen, builder, expansion, target, scope,
                                       emitted)

            # an expression macro substitutes its expression in place
            return emit_expression(gen, builder, expansion, None, scope)

    # the builtin 'enumerate(x)' resolves to its '__enumerate' instance
    if call.name == "enumerate":
        from siec.codegen.methods import rewrite_enumerate

        if (rewritten := rewrite_enumerate(gen, call, scope)) is not None:
            call = rewritten

    # a dotted name is a method on its receiver chain, or resolves
    # through the file's module bindings; a scoped receiver shadows any
    # module prefix
    receiver = None
    if "::" in call.name:
        # 'S::method(s)' passes its receiver explicitly, and a static's
        # arguments pass as-is; the type name resolves like any written
        # type, so it may carry dotted generic arguments
        symbol = qualified_method(gen, call.name)
        if symbol is None:
            from siec.codegen.deprecation import check_removed_method

            base = call.name.partition("::")[0]

            # a removed method leaves no declaration to resolve; its
            # name still answers for the advice
            check_removed_method(gen, base, call.name.partition("::")[2])
            raise NameError(f"type {base!r} has no method "
                            f"{call.name.partition('::')[2]!r}")
    elif "." in call.name:
        symbol = None
        if call.name.split(".", 1)[0] in scope:
            if (found := method_call(gen, call, scope)) is not None:
                symbol, receiver = found

        if symbol is None:
            symbol = gen.resolve_qualified(call.name.split("."))

        if symbol is None and (found := method_call(gen, call, scope)) is not None:
            symbol, receiver = found

        if symbol is None:
            from siec.codegen.deprecation import check_removed_method
            from siec.codegen.inference import expr_sie_type

            # a removed method leaves no declaration to resolve; the
            # receiver's type still names it for the advice
            base, _, method = call.name.rpartition(".")
            if base in scope:
                check_removed_method(gen, expr_sie_type(gen, Var(base), scope),
                                     method)

            raise NameError(f"undefined function {call.name!r}")

        if receiver is None and symbol in gen.globals:
            return emit_indirect_call(gen, builder, call, scope, symbol)
    else:
        # a name in scope is always in view; anything else must be visible
        # to this file: an imported module's names need their qualified
        # spelling or a member import. A name carrying '<' is a resolved
        # instance the compiler wrote; no file's view gates it
        if (call.name not in scope and "<" not in call.name
                and not gen.sees(call.name)):
            raise NameError(f"undefined function {call.name!r}")

        # a variable or global holding a function reference is called through
        # its value; the current file's statics resolve first, other files' never
        symbol = gen.resolve_symbol(call.name)
        if call.name in scope or symbol in gen.globals:
            return emit_indirect_call(gen, builder, call, scope)

    # a removed generic leaves no template to resolve, so its name is
    # checked before the lookups that would call it undefined
    from siec.codegen.deprecation import check_removed

    check_removed(gen, symbol)

    # the sugar form passes the receiver as the hidden first argument
    if receiver is not None:
        call = Call(call.name, [receiver, *call.args], call.type_args)

    # an overloaded name resolves to the candidate its arguments pick; a
    # fit bypasses a generic template sharing the name, while a call no
    # concrete candidate takes falls through to it
    picked = False
    if symbol in gen.overloads:
        try:
            symbol = pick_overload(gen, symbol, call.args, scope)
            picked = True
        except TypeError:
            if gen.generic_functions.get(symbol) is None:
                raise

    # a stamped overload's body waits for its first picked call
    from siec.codegen.worklist import activate_function_instance

    activate_function_instance(gen, symbol)

    # a generic callee instantiates for this call's type arguments,
    # explicit, inferred, or driven by the expected result type; the
    # call's shape picks among arity overloads
    if not picked and gen.generic_functions.get(symbol) is not None:
        template, type_args = pick_generic_call(gen, symbol, call, scope,
                                                expected)
        symbol = instantiate_function(gen, template, type_args)
    elif not isinstance(gen.module.globals.get(symbol), ir.Function):
        # no function by this name: 'S(...)' may construct a struct
        from siec.codegen.methods import constructor_type, emit_constructor

        if (ctor := constructor_type(gen, call, symbol)) is not None:
            return emit_constructor(gen, builder, ctor, call, scope, as_address)

        if call.type_args is not None:
            raise TypeError(f"function {call.name!r} is not generic")
    elif call.type_args is not None:
        raise TypeError(f"function {call.name!r} is not generic")

    # look up the callee among the module's declared functions
    func = gen.module.globals.get(symbol)
    if not isinstance(func, ir.Function):
        raise NameError(f"undefined function {call.name!r}")

    # the caller reaches this callee: an edge for the deprecation walk
    from siec.codegen.deprecation import note_use

    note_use(gen, symbol)

    # only a reference-returning call has an address to keep
    if as_address and not is_reference(gen.return_types.get(func.name)):
        raise TypeError("cannot take the address of a call's value")

    # check arity, letting varargs functions take extra arguments; an
    # indirect struct return hides its own first parameter
    ret_lowering = gen.abi_returns.get(func.name)
    hidden = 1 if ret_lowering is not None and ret_lowering[0] == "indirect" else 0
    expected = len(func.function_type.args) - hidden

    # a 'name...' variadic packs the call's extra arguments into its
    # trailing const Any[]; an explicit Any[] argument forwards as-is
    if func.name in gen.variadics:
        call = pack_variadic(gen, call, expected, scope)

    # trailing parameters with defaults are optional at the call
    defaults, defaults_file = gen.param_defaults.get(func.name, ([], None))
    required = expected
    while (required and required <= len(defaults)
           and defaults[required - 1] is not None):
        required -= 1

    if len(call.args) < required:
        raise TypeError(f"too few arguments to function {call.name!r}")

    if len(call.args) > expected and not func.function_type.var_arg:
        raise TypeError(f"too many arguments to function {call.name!r}")

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
    from siec.codegen.inference import enum_backing, numeric_class, type_info
    from siec.codegen.ownership import (TemporaryDrop, destroyable,
                                       expression_returns_reference)

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
        slot = entry_alloca(builder, value.type, "ref.spill")
        builder.store(value, slot)
        from siec.codegen.ownership import (expression_identity,
                                           temporary_registered)

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

    result = builder.call(callee, args)

    # a reference-returning callee yields the value's address; reading
    # the call as a value loads through it
    if is_reference(fn_type_parts(var_type)[1] or ""):
        return builder.load(result)

    return result
