"""Declaration and emission of functions."""

from llvmlite import ir

from siec.ast import Function
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator, Variable
from siec.codegen.statements import emit_block
from siec.codegen.types import resolve_type, strip_const


def declare_function(gen: CodeGenerator, fn: Function) -> ir.Function:
    """
    Declare a function in the module, reusing a matching earlier declaration.
    """
    with source_location(line=fn.line, file=fn.file):
        return declare_function_body(gen, fn)


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
    if fn.name == "main" and fn.return_type is None:
        ret_type = ir.IntType(32)
    else:
        ret_type = resolve_type(fn.return_type, gen.structs)

    if main_takes_args(fn):
        param_types = [ir.IntType(32), resolve_type("char**", gen.structs)]
    else:
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
        if main_takes_args(fn):
            spill_main_args(gen, builder, fn, func, scope)
        else:
            for arg, param in zip(func.args, fn.params):
                arg.name = param.name
                scope[param.name] = Variable(
                    builder.alloca(arg.type, name=f"{param.name}.addr"), param.type)
                builder.store(arg, scope[param.name].slot)

        # emit the body statements starting from the entry block
        emit_block(gen, builder, fn.body, scope)

        # a void function may fall off the end, and so may main, whose
        # implicit exit code is 0; anything else must return
        if not builder.block.is_terminated:
            if isinstance(ret_type, ir.VoidType):
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
