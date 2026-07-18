"""Registration of '@extern let' and '@static let' global variables."""

from llvmlite import ir

from siec.ast import (
    AggregateLiteral,
    BoolLiteral,
    Expr,
    FloatLiteral,
    Global,
    NullLiteral,
    Program,
    StrLiteral,
)
from siec.codegen.aliases import expand_alias
from siec.codegen.enums import evaluate, evaluate_size
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator
from siec.codegen.types import is_const, is_reference, resolve_type, sized_array, strip_const


def register_globals(gen: CodeGenerator, program: Program) -> None:
    """
    Declare every module-level variable: '@extern let' as external linkage,
    its storage defined and initialized outside this program; '@static let'
    as file-local storage defined here, under a mangled symbol its own file
    resolves to, like a static function's.
    """
    for glob in program.globals:
        with source_location(line=glob.line, file=glob.file):
            glob.type = expand_alias(gen, glob.type)

            symbol = glob.name
            if glob.is_static:
                key = (glob.file, glob.name)
                if key in gen.statics:
                    raise TypeError(f"global {glob.name!r} is declared more than once")

                gen.statics[key] = symbol = f"{glob.name}.static.{len(gen.statics)}"

            if symbol in gen.globals or symbol in gen.module.globals:
                raise TypeError(f"global {glob.name!r} is declared more than once")

            if is_reference(glob.type):
                raise TypeError("a reference cannot type a variable")

            var = ir.GlobalVariable(gen.module, resolve_type(glob.type, gen.structs),
                                    name=symbol)

            # an '@align(N)' struct's storage honors the declared alignment
            if (align := gen.struct_align(glob.type)) is not None:
                var.align = align

            if glob.is_static:
                var.linkage = "internal"
                var.initializer = global_initializer(gen, glob, symbol)
            else:
                var.linkage = "external"

            # a sized array declares an 'X[]', the size only directing its
            # backing — the same canonical type a local declaration records
            sie_type = glob.type
            if (sized := sized_array(strip_const(sie_type))) is not None:
                sie_type = f"const {sized[0]}" if is_const(sie_type) else sized[0]

            gen.globals[symbol] = sie_type


def global_initializer(gen: CodeGenerator, glob: Global, symbol: str) -> ir.Constant:
    """
    Build a static global's initial value: zero when none is given, a
    compile-time constant otherwise.
    """
    type_ = resolve_type(glob.type, gen.structs)

    # a sized array 'X[N]' points at N zeroed elements of module storage,
    # its length N, the module-level shape of a local sized declaration
    if (sized := sized_array(strip_const(glob.type))) is not None:
        if glob.value is not None:
            raise TypeError(f"a sized array takes its contents from its size; "
                            f"initialize an {sized[0]!r} instead")

        size = evaluate_size(gen, sized[1])
        element = type_.elements[0].pointee
        backing = ir.GlobalVariable(gen.module, ir.ArrayType(element, size),
                                    name=f"{symbol}.backing")
        backing.linkage = "internal"
        backing.initializer = ir.Constant(backing.value_type, None)

        zero = ir.Constant(ir.IntType(32), 0)
        return ir.Constant(type_, [backing.gep([zero, zero]),
                                   ir.Constant(ir.IntType(64), size)])

    if glob.value is None:
        return ir.Constant(type_, None)  # zero-initialized, C-style

    return constant_value(gen, glob.value, type_, glob.type)


def constant_value(gen: CodeGenerator, expr: Expr, type_: ir.Type,
                   sie_type: str) -> ir.Constant:
    """
    Evaluate an initializer to a compile-time constant of the given type.
    """
    if isinstance(expr, AggregateLiteral):
        return constant_aggregate(gen, expr, type_, sie_type)

    if isinstance(expr, FloatLiteral):
        return ir.Constant(type_, expr.value)

    if isinstance(expr, BoolLiteral):
        return ir.Constant(type_, 1 if expr.value else 0)

    if isinstance(expr, NullLiteral):
        if not isinstance(type_, ir.PointerType):
            raise TypeError(f"'null' cannot initialize a {sie_type!r} value")

        return ir.Constant(type_, None)

    # a string initializer points at a private string constant
    if isinstance(expr, StrLiteral):
        if strip_const(sie_type) != "char*":
            raise TypeError(f"cannot initialize a {sie_type!r} value with a string")

        return string_constant(gen, expr.value).bitcast(type_)

    # anything else must evaluate to an integer at compile time
    return ir.Constant(type_, evaluate(gen, expr))


def constant_aggregate(gen: CodeGenerator, literal: AggregateLiteral,
                       type_: ir.Type, sie_type: str) -> ir.Constant:
    """
    Build a struct's constant initial value from an aggregate literal:
    positional fields fill in order, named fields wherever they sit, and
    fields a named literal leaves out start at zero.
    """
    info = gen.structs.get(strip_const(sie_type))
    if info is None or not info.fields:
        raise TypeError(f"aggregate initializer needs a struct type, not {sie_type!r}")

    fields = info.fields
    values = [ir.Constant(field_type, None) for field_type in type_.elements]

    if literal.names is None:
        if len(literal.elements) != len(fields):
            raise TypeError(f"aggregate literal has {len(literal.elements)} "
                            f"elements, expected {len(fields)}")

        pairs = list(enumerate(literal.elements))
    else:
        index_of = {field.name: index for index, field in enumerate(fields)}

        pairs = []
        for name, element in zip(literal.names, literal.elements):
            if name not in index_of:
                raise TypeError(f"aggregate literal names unknown field {name!r}")

            if any(index == index_of[name] for index, _ in pairs):
                raise TypeError(f"aggregate literal sets field {name!r} more than once")

            pairs.append((index_of[name], element))

    for index, element in pairs:
        values[index] = constant_value(gen, element, type_.elements[index],
                                       fields[index].type)

    return ir.Constant(type_, values)


def string_constant(gen: CodeGenerator, text: str) -> ir.GlobalVariable:
    """
    Store a string's bytes as a private, null-terminated module constant.
    """
    data = text.encode() + b"\0"
    array_type = ir.ArrayType(ir.IntType(8), len(data))

    const = ir.GlobalVariable(gen.module, array_type, name=f".str.{gen.str_count}")
    const.global_constant = True
    const.linkage = "private"
    const.initializer = ir.Constant(array_type, bytearray(data))

    gen.str_count += 1
    return const
