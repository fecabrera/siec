"""Checking and lowering of lexically capturing function values."""

from llvmlite import ir

from siec.ast import Function
from siec.codegen.generator import Variable, entry_alloca
from siec.codegen.types import fn_type_parts, resolve_type, strip_const


def closure_type(expr) -> str:
    """The canonical callable type carried by a closure expression."""
    params = ",".join(param.type for param in expr.params)
    result = f"closure fn({params})"
    if expr.return_type is not None:
        result += f"->{expr.return_type}"
    return result


def check_closure(gen, expr, scope: dict) -> str:
    """Check a closure body in its lexical scope and record its captures."""
    from siec.codegen.aliases import expand_alias
    from siec.codegen.checking import check_block, checked_variable
    from siec.codegen.types import validate_type

    expr.file = expr.file or gen.current_file
    expr.return_type = expand_alias(gen, expr.return_type)
    for param in expr.params:
        param.type = expand_alias(gen, param.type)
        validate_type(param.type, gen.structs)
    validate_type(expr.return_type, gen.structs)

    expr.captures = {
        name: variable.type
        for name, variable in scope.items()
        if name != expr.name and not name.startswith(".")
    }
    inner = dict(scope)
    inner.update({param.name: checked_variable(param.type)
                  for param in expr.params})
    synthetic = Function(
        expr.name or "<closure>", expr.params, expr.return_type,
        expr.body, line=expr.line, file=expr.file)
    terminates = check_block(gen, expr.body, inner, synthetic)
    if expr.return_type is not None and not terminates:
        raise TypeError(f"closure {expr.name or '<anonymous>'!r} must return "
                        "a value")
    return closure_type(expr)


def emit_closure(gen, builder: ir.IRBuilder, expr, scope: dict):
    """Build a stack environment and its erased closure pair."""
    params, ret, _ = fn_type_parts(closure_type(expr))
    opaque = ir.PointerType(ir.IntType(8))
    invoke_type = ir.FunctionType(
        resolve_type(ret, gen.structs),
        [opaque, *(resolve_type(param, gen.structs) for param in params)],
    )
    serial = getattr(gen, "closure_count", 0)
    gen.closure_count = serial + 1
    invoke = ir.Function(gen.module, invoke_type,
                         name=f".closure.{serial}")
    invoke.linkage = "internal"

    captured = [(name, scope[name]) for name in expr.captures
                if name in scope]
    env_type = ir.LiteralStructType([opaque] * (1 + len(captured)))
    env = entry_alloca(builder, env_type, f"closure.env.{serial}")
    erased_invoke = builder.bitcast(invoke, opaque)
    builder.store(erased_invoke, builder.gep(
        env, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)]))
    for index, (_, variable) in enumerate(captured, 1):
        pointer = builder.bitcast(variable.slot, opaque)
        builder.store(pointer, builder.gep(
            env, [ir.Constant(ir.IntType(32), 0),
                  ir.Constant(ir.IntType(32), index)]))

    block = invoke.append_basic_block("entry")
    inner_builder = ir.IRBuilder(block)
    env_arg = invoke.args[0]
    env_arg.name = "env"
    typed_env = inner_builder.bitcast(env_arg, ir.PointerType(env_type))
    inner_scope = {}
    for index, (name, variable) in enumerate(captured, 1):
        erased = inner_builder.load(inner_builder.gep(
            typed_env, [ir.Constant(ir.IntType(32), 0),
                        ir.Constant(ir.IntType(32), index)]))
        slot = inner_builder.bitcast(erased, variable.slot.type)
        inner_scope[name] = Variable(slot, variable.type,
                                     drop_flag=variable.drop_flag)

    for arg, param in zip(invoke.args[1:], expr.params):
        arg.name = param.name
        if param.type.startswith("&") or param.type.startswith("const &"):
            inner_scope[param.name] = Variable(arg, param.type)
        else:
            slot = inner_builder.alloca(arg.type, name=f"{param.name}.addr")
            inner_builder.store(arg, slot)
            inner_scope[param.name] = Variable(slot, param.type)

    from siec.codegen.statements import emit_block

    previous_function = gen.current_function
    previous_file = gen.current_file
    previous_frames = gen.defer_frames
    gen.current_function = invoke.name
    gen.current_file = expr.file
    gen.return_types[invoke.name] = expr.return_type
    gen.defer_frames = []
    try:
        emit_block(gen, inner_builder, expr.body, inner_scope)
        if not inner_builder.block.is_terminated:
            inner_builder.ret_void()
    finally:
        gen.defer_frames = previous_frames
        gen.current_function = previous_function
        gen.current_file = previous_file

    closure = ir.Constant(resolve_type(closure_type(expr), gen.structs), None)
    closure = builder.insert_value(closure, erased_invoke, 0)
    return builder.insert_value(closure, builder.bitcast(env, opaque), 1)


def emit_closure_call(gen, builder, value, type_name: str, args, scope):
    """Invoke a closure pair, passing its environment as the hidden argument."""
    from siec.codegen.calls import emit_argument

    params, ret, suffix = fn_type_parts(strip_const(type_name))
    if suffix:
        raise TypeError("cannot call a derived closure value")
    if len(args) != len(params):
        raise TypeError(f"closure takes {len(params)} arguments, got {len(args)}")
    opaque = ir.PointerType(ir.IntType(8))
    code = builder.extract_value(value, 0, name="closure.code")
    env = builder.extract_value(value, 1, name="closure.env")
    signature = ir.PointerType(ir.FunctionType(
        resolve_type(ret, gen.structs),
        [opaque, *(resolve_type(param, gen.structs) for param in params)]))
    callee = builder.bitcast(code, signature)
    values = [emit_argument(gen, builder, arg, param, scope)
              for arg, param in zip(args, params)]
    result = builder.call(callee, [env, *values])
    if (ret or "").startswith("&"):
        return builder.load(result)
    return result


def validate_callback_adapter(closure_name: str, abi_name: str) -> None:
    """Validate the raw ABI a closure may adapt to through ``as``."""
    closure_params, closure_ret, closure_suffix = fn_type_parts(closure_name)
    abi_params, abi_ret, abi_suffix = fn_type_parts(abi_name)
    if closure_suffix or abi_suffix:
        raise TypeError("a callback adapter needs plain callable types")
    if not abi_params or strip_const(abi_params[-1]) != "opaque*":
        raise TypeError("a closure callback ABI must end in an 'opaque*' "
                        "environment parameter")
    if len(closure_params) > len(abi_params) - 1:
        raise TypeError("a callback ABI does not provide all closure arguments")
    if closure_params != abi_params[:len(closure_params)]:
        raise TypeError("a callback ABI's leading parameters must match the "
                        "closure parameters")
    if closure_ret != abi_ret:
        raise TypeError("a callback ABI must return the closure's return type")


def emit_callback_adapter(gen, builder, closure_name: str, abi_name: str):
    """Return a raw ABI thunk that invokes the closure stored in user data."""
    validate_callback_adapter(closure_name, abi_name)
    closure_params, closure_ret, _ = fn_type_parts(closure_name)
    abi_params, abi_ret, _ = fn_type_parts(abi_name)
    cache = getattr(gen, "closure_adapters", None)
    if cache is None:
        cache = gen.closure_adapters = {}
    key = (closure_name, abi_name)
    if key in cache:
        return cache[key]

    opaque = ir.PointerType(ir.IntType(8))
    abi_type = ir.FunctionType(
        resolve_type(abi_ret, gen.structs),
        [resolve_type(param, gen.structs) for param in abi_params])
    serial = len(cache)
    adapter = ir.Function(gen.module, abi_type,
                          name=f".closure.adapter.{serial}")
    adapter.linkage = "internal"
    inner = ir.IRBuilder(adapter.append_basic_block("entry"))
    env = adapter.args[-1]
    env.name = "env"
    header = inner.bitcast(env, ir.PointerType(opaque))
    erased_code = inner.load(header, name="closure.code")
    invoke_type = ir.PointerType(ir.FunctionType(
        resolve_type(closure_ret, gen.structs),
        [opaque, *(resolve_type(param, gen.structs)
                   for param in closure_params)]))
    invoke = inner.bitcast(erased_code, invoke_type)
    result = inner.call(
        invoke, [inner.bitcast(env, opaque),
                 *adapter.args[:len(closure_params)]])
    if closure_ret is None:
        inner.ret_void()
    else:
        inner.ret(result)
    cache[key] = adapter
    return adapter
