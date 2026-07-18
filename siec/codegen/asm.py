"""Emission of '@asm' assembly, decorated functions and inline blocks alike."""

from llvmlite import ir

from siec.ast import AsmBlock, Function, Var
from siec.codegen.aliases import expand_alias
from siec.codegen.constants import target_arch
from siec.codegen.generator import CodeGenerator
from siec.codegen.types import resolve_type

NAME_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"


def translate(body: str, operands: dict[str, int]) -> str:
    """
    Rewrite a Sie assembly body into LLVM's template language: '$name' and
    '${name}' become the operand's number, '${name:mod}' keeps its modifier,
    and any other '$' (an x86 immediate, say) escapes to '$$'.
    """
    out = []
    i = 0
    while i < len(body):
        char = body[i]
        if char != "$":
            out.append(char)
            i += 1
            continue

        # '${name[:mod]}': the braces bound the name, the modifier rides along
        if body[i + 1:i + 2] == "{":
            end = body.find("}", i)
            if end == -1:
                raise TypeError("unterminated '${' in assembly body")

            name, _, modifier = body[i + 2:end].partition(":")
            number = operand_number(name, operands)
            out.append(f"${{{number}:{modifier}}}" if modifier else f"${number}")
            i = end + 1
            continue

        # '$name': the name runs to the first non-identifier character
        j = i + 1
        while j < len(body) and body[j] in NAME_CHARS:
            j += 1

        name = body[i + 1:j]
        if name and not name[0].isdigit():
            out.append(f"${operand_number(name, operands)}")
        else:
            # a bare or numeric '$' is the assembly's own, escaped for LLVM
            out.append("$$")
            j = i + 1

        i = j

    return "".join(out)


def operand_number(name: str, operands: dict[str, int]) -> int:
    """
    Look up an interpolated name's operand number.
    """
    if name not in operands:
        raise TypeError(f"unknown assembly operand {name!r}")

    return operands[name]


def register_class(gen: CodeGenerator, type_: ir.Type) -> str:
    """
    The constraint letter for a value's register class: 'r' for integers
    and pointers, the target's float class for floats.
    """
    if isinstance(type_, (ir.FloatType, ir.DoubleType)):
        return "x" if target_arch(gen.target) == "ARCH_X86_64" else "w"

    if isinstance(type_, (ir.IntType, ir.PointerType)):
        return "r"

    raise TypeError(f"cannot pass a value of type {type_} to assembly; "
                    "only scalars and pointers travel in registers")


def emit_asm(gen: CodeGenerator, builder: ir.IRBuilder, body: str,
             ret_type: ir.Type, values: list, names: list[str],
             clobbers: list[str]):
    """
    Emit an inline-assembly call: the values as register inputs, '$out' as
    the register output when one is returned, clobbers appended.
    """
    returns = not isinstance(ret_type, ir.VoidType)

    operands = {}
    if returns:
        operands["out"] = 0

    for i, name in enumerate(names):
        if name in operands:
            raise TypeError(f"duplicate assembly operand {name!r}")

        operands[name] = len(operands)

    constraints = []
    if returns:
        constraints.append(f"={register_class(gen, ret_type)}")

    constraints += [register_class(gen, value.type) for value in values]
    constraints += [f"~{{{clobber}}}" for clobber in clobbers]

    asm = ir.InlineAsm(ir.FunctionType(ret_type, [value.type for value in values]),
                       translate(body.strip(), operands), ",".join(constraints),
                       side_effect=True)

    return builder.call(asm, values)


def emit_asm_block(gen: CodeGenerator, builder: ir.IRBuilder, expr: AsmBlock,
                   scope: dict):
    """
    Emit an inline '@asm' block: operands read from the enclosing scope by
    name, the result typed by the block's '-> T'.
    """
    # deferred import: expressions and asm are mutually recursive
    from siec.codegen.expressions import emit_expression

    expr.return_type = expand_alias(gen, expr.return_type)
    ret_type = resolve_type(expr.return_type, gen.structs)

    values = [emit_expression(gen, builder, Var(name), None, scope)
              for name in expr.args]

    return emit_asm(gen, builder, expr.body, ret_type, values, expr.args,
                    expr.clobbers)


def emit_asm_function(gen: CodeGenerator, builder: ir.IRBuilder, fn: Function,
                      func: ir.Function) -> None:
    """
    Emit an '@asm' function's body: its parameters feed the assembly
    directly, and the assembly's output is its return value.
    """
    for arg, param in zip(func.args, fn.params):
        arg.name = param.name

    result = emit_asm(gen, builder, fn.asm, func.function_type.return_type,
                      list(func.args), [param.name for param in fn.params],
                      fn.clobbers)

    if isinstance(func.function_type.return_type, ir.VoidType):
        builder.ret_void()
    else:
        builder.ret(result)
