"""Resolution and lowering of module-level variables."""

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
from siec.codegen.inference import check_field_access
from siec.codegen.types import (
    is_const,
    is_reference,
    resolve_type,
    sized_array,
    strip_const,
    validate_type,
)


def resolve_globals(gen: CodeGenerator, program: Program) -> None:
    """
    Resolve every module-level variable without constructing LLVM storage.
    """
    for glob in program.globals:
        with source_location(line=glob.line, file=glob.file):
            gen.current_file = glob.file
            glob.type = expand_alias(gen, glob.type)

            symbol = glob.name
            if glob.is_static:
                key = (glob.file, glob.name)
                if key in gen.statics:
                    raise TypeError(f"global {glob.name!r} is declared more than once")

                gen.statics[key] = symbol = f"{glob.name}.static.{len(gen.statics)}"
            elif glob.symbol is not None:
                # an '@symbol' global lives under its chosen outside symbol,
                # its Sie name resolving there from everywhere
                if gen.symbol_names.get(glob.name, glob.symbol) != glob.symbol:
                    raise TypeError(f"conflicting '@symbol' names for global {glob.name!r}")

                gen.symbol_names[glob.name] = symbol = glob.symbol
                gen.symbol_files[glob.name] = glob.file

            if symbol in gen.globals:
                raise TypeError(f"global {glob.name!r} is declared more than once")

            if is_reference(glob.type):
                raise TypeError("a reference cannot type a variable")

            validate_type(glob.type, gen.structs)
            validate_global_initializer(gen, glob)

            # a sized array declares an 'X[]', the size only directing its
            # backing - the same canonical type a local declaration records
            sie_type = glob.type
            if (sized := sized_array(strip_const(sie_type))) is not None:
                sie_type = f"const {sized[0]}" if is_const(sie_type) else sized[0]

            gen.globals[symbol] = sie_type
            gen.resolved_globals[symbol] = glob


def validate_global_initializer(gen: CodeGenerator, glob: Global) -> None:
    """Validate initializer-only rules that do not require LLVM values."""
    if (sized := sized_array(strip_const(glob.type))) is not None:
        if glob.value is not None:
            raise TypeError(f"a sized array takes its contents from its size; "
                            f"initialize an {sized[0]!r} instead")
        evaluate_size(gen, sized[1])
        return

    if glob.is_static and glob.value is not None:
        validate_constant_value(gen, glob.value, glob.type)


def validate_constant_value(gen: CodeGenerator, expr: Expr,
                            sie_type: str) -> None:
    """Check a static initializer without constructing an LLVM constant."""
    target = strip_const(sie_type)

    if isinstance(expr, AggregateLiteral):
        info = gen.structs.get(target)
        if info is None or not info.fields:
            raise TypeError(
                f"aggregate initializer needs a struct type, not "
                f"{sie_type!r}")
        if info.is_union:
            raise TypeError("a union takes no aggregate literal; assign one "
                            "of its fields instead")

        if expr.names is None:
            if len(expr.elements) != len(info.fields):
                raise TypeError(
                    f"aggregate literal has {len(expr.elements)} elements, "
                    f"expected {len(info.fields)}")
            pairs = zip(info.fields, expr.elements)
        else:
            fields = {field.name: field for field in info.fields}
            seen = set()
            pairs = []
            for name, value in zip(expr.names, expr.elements):
                if name not in fields:
                    raise TypeError(
                        f"aggregate literal names unknown field {name!r}")
                if name in seen:
                    raise TypeError(
                        f"aggregate literal sets field {name!r} more than "
                        "once")
                seen.add(name)
                pairs.append((fields[name], value))

        for field, value in pairs:
            check_field_access(gen, target, field)
            validate_constant_value(gen, value, field.type)
        return

    if isinstance(expr, FloatLiteral):
        if target not in ("f32", "f64"):
            raise TypeError(
                f"cannot initialize a {sie_type!r} value with a float")
        return

    if isinstance(expr, BoolLiteral):
        if target not in ("bool", "i8", "i16", "i32", "i64",
                          "u8", "u16", "u32", "u64"):
            raise TypeError(
                f"cannot initialize a {sie_type!r} value with a bool")
        return

    if isinstance(expr, NullLiteral):
        if not target.endswith("*"):
            raise TypeError(
                f"'null' cannot initialize a {sie_type!r} value")
        return

    if isinstance(expr, StrLiteral):
        if target not in ("char*", "char[]"):
            raise TypeError(
                f"cannot initialize a {sie_type!r} value with a string")
        return

    # The remaining supported forms are compile-time integer expressions:
    # literals, constants, enum members, and their arithmetic.
    info = gen.structs.get(target)
    if (info is not None and info.backing is None
            or target.endswith("[]")
            or target.startswith("raw<")
            or target.startswith("fn(")):
        raise TypeError(
            f"cannot initialize a {sie_type!r} value with an integer")
    evaluate(gen, expr)


def lower_globals(gen: CodeGenerator) -> None:
    """Materialize the checked global inventory as LLVM storage."""
    for symbol, glob in gen.resolved_globals.items():
        var = ir.GlobalVariable(
            gen.module,
            resolve_type(glob.type, gen.structs),
            name=symbol,
        )

        if (align := gen.struct_align(glob.type)) is not None:
            var.align = align

        if glob.is_static:
            var.linkage = "internal"
            var.initializer = global_initializer(gen, glob, symbol)
        else:
            var.linkage = "external"


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

    # a string initializer points at a private string constant: bare for
    # a 'char*', the fat array for a 'char[]', its length excluding the
    # null terminator like any string literal's
    if isinstance(expr, StrLiteral):
        stripped = strip_const(sie_type)
        if stripped == "char[]":
            zero = ir.Constant(ir.IntType(32), 0)
            data = gen.string_constant(expr.value)
            return ir.Constant(type_, [data.gep([zero, zero]),
                                       ir.Constant(ir.IntType(64),
                                                   len(expr.value.encode()))])

        if stripped != "char*":
            raise TypeError(f"cannot initialize a {sie_type!r} value with a string")

        return gen.string_constant(expr.value).bitcast(type_)

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

    if info.is_union:
        raise TypeError("a union takes no aggregate literal; assign one "
                        "of its fields instead")

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
        check_field_access(gen, sie_type, fields[index])
        values[index] = constant_value(gen, element, type_.elements[index],
                                       fields[index].type)

    return ir.Constant(type_, values)
