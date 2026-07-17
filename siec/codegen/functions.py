"""Declaration and emission of functions."""

from llvmlite import ir

from ..ast import Function
from .errors import source_location
from .generator import CodeGenerator, Variable
from .statements import emit_block
from .types import resolve_type


def declare_function(gen: CodeGenerator, fn: Function) -> ir.Function:
    """
    Declare a function in the module, reusing a matching earlier declaration.
    """
    with source_location(line=fn.line, file=fn.file):
        return declare_function_body(gen, fn)


def declare_function_body(gen: CodeGenerator, fn: Function) -> ir.Function:
    """
    Build the function's declaration from its annotated Sie signature.
    """
    ret_type = resolve_type(fn.return_type, gen.structs)
    param_types = [resolve_type(p.type, gen.structs) for p in fn.params]
    func_type = ir.FunctionType(ret_type, param_types, var_arg=fn.var_arg)

    gen.return_types[fn.name] = fn.return_type
    gen.param_types[fn.name] = [p.type for p in fn.params]

    # redeclarations are allowed as long as the signature matches
    existing = gen.module.globals.get(fn.name)
    if existing is not None:
        if existing.function_type != func_type:
            raise TypeError(f"conflicting declarations for function {fn.name!r}")
        
        return existing

    return ir.Function(gen.module, func_type, name=fn.name)


def emit_function(gen: CodeGenerator, fn: Function) -> None:
    """
    Emit the body of a defined function into its declaration, tagging errors with its line.

    A nested statement tags its own line first, so the function line only fills
    in for errors raised outside any statement (a missing return, say).
    """
    with source_location(line=fn.line, file=fn.file):
        # a declaration that already has blocks was defined elsewhere
        func = gen.module.globals[fn.name]
        if func.blocks:
            raise TypeError(f"function {fn.name!r} is defined more than once")

        ret_type = func.function_type.return_type
        builder = ir.IRBuilder(func.append_basic_block("entry"))

        # the scope maps each name to a typed stack slot; spill the parameters into theirs
        scope = {}
        for arg, param in zip(func.args, fn.params):
            arg.name = param.name
            scope[param.name] = Variable(
                builder.alloca(arg.type, name=f"{param.name}.addr"), param.type)
            builder.store(arg, scope[param.name].slot)

        # emit the body statements starting from the entry block
        emit_block(gen, builder, fn.body, scope)

        # a void function may fall off the end; anything else must return
        if not builder.block.is_terminated:
            if isinstance(ret_type, ir.VoidType):
                builder.ret_void()
            else:
                raise TypeError(f"function {fn.name!r} must return a value")
