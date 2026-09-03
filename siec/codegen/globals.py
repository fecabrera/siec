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
from siec.codegen.aggregates import resolve_aggregate
from siec.codegen.arrays import empty_array_value
from siec.codegen.enums import evaluate, evaluate_size
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator
from siec.codegen.types import (
    is_const,
    INTEGER_TYPES,
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
            if glob.type is None:
                from siec.codegen.inference import infer_type, untyped_reason

                glob.type = infer_type(gen, glob.value, {})
                if glob.type is None:
                    if (reason := untyped_reason(
                            gen, glob.value, {})) is not None:
                        raise reason

                    raise TypeError(f"cannot infer a type for {glob.name!r}: "
                                    "annotate it explicitly")
            else:
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
    elif glob.is_static:
        validate_constant_default_type(gen, glob.type)


def validate_constant_value(gen: CodeGenerator, expr: Expr,
                            sie_type: str) -> None:
    """Check a static initializer without constructing an LLVM constant."""
    target = strip_const(sie_type)

    if isinstance(expr, AggregateLiteral):
        plan = resolve_aggregate(gen, expr, sie_type)
        expr.aggregate_plan = plan
        for element in plan.elements:
            validate_constant_value(gen, element.value, element.target)
        for omitted in plan.omitted:
            validate_constant_field_default(gen, omitted.field)
        return

    if isinstance(expr, FloatLiteral):
        if target not in ("f32", "f64"):
            raise TypeError(
                f"cannot initialize a {sie_type!r} value with a float")
        return

    if isinstance(expr, BoolLiteral):
        if target != "bool" and target not in INTEGER_TYPES:
            raise TypeError(
                f"cannot initialize a {sie_type!r} value with a bool")
        return

    if isinstance(expr, NullLiteral):
        from siec.codegen.types import is_nonnull_pointer

        if is_nonnull_pointer(target):
            raise TypeError("null cannot initialize a non-null pointer")
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


def validate_constant_field_default(gen: CodeGenerator, field) -> None:
    """Validate one field default used by a static aggregate."""
    if field.default is not None:
        validate_constant_value(gen, field.default, field.type)
    else:
        validate_constant_default_type(gen, field.type)


def validate_constant_default_type(gen: CodeGenerator, type_name: str,
                                   seen: set[str] | None = None) -> None:
    """Validate defaults recursively used by static initialization."""
    from siec.codegen.inference import type_info

    canonical = strip_const(type_name)
    seen = set() if seen is None else seen
    if canonical in seen:
        return
    seen.add(canonical)
    info = type_info(gen, canonical)
    if info is None or info.fields is None or info.is_union:
        return
    for field in info.fields:
        if field.default is not None:
            validate_constant_value(gen, field.default, field.type)
        else:
            validate_constant_default_type(gen, field.type, seen)


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
        return default_constant(gen, type_, glob.type)

    return constant_value(gen, glob.value, type_, glob.type)


def default_constant(gen: CodeGenerator, type_: ir.Type,
                     type_name: str) -> ir.Constant:
    """Build a zero-like constant that preserves array non-null invariants."""
    canonical = strip_const(type_name)
    if canonical.endswith("[]"):
        return empty_array_value(gen, type_)

    info = gen.structs.get(canonical)
    if (info is None or info.fields is None or info.is_union
            or not hasattr(type_, "elements")):
        return ir.Constant(type_, None)

    return ir.Constant(type_, [
        constant_field_default(gen, field_type, field)
        for field_type, field in zip(type_.elements, info.fields)
    ])


def constant_field_default(gen: CodeGenerator, field_type: ir.Type,
                           field) -> ir.Constant:
    """Build one declared or recursive field default as a constant."""
    if field.default is not None:
        return constant_value(gen, field.default, field_type, field.type)
    return default_constant(gen, field_type, field.type)


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
    Build a constant aggregate from its checked field mapping and defaults.
    """
    from siec.codegen.hir import checked_aggregate

    plan = checked_aggregate(literal)
    if plan is None:
        plan = resolve_aggregate(gen, literal, sie_type)

    if plan.is_union:
        selected_plan = plan.elements[0]
        field = selected_plan.field
        element = selected_plan.value
        field_type = resolve_type(field.type, gen.structs)
        selected = constant_value(
            gen, element, field_type, selected_plan.target)
        storage = type_.elements[0]

        if selected.type == storage:
            selected_storage = selected
        elif (isinstance(selected.type, ir.IntType)
              and isinstance(storage, ir.IntType)
              and selected.type.width < storage.width):
            selected_storage = selected.zext(storage)
        elif (not isinstance(selected.type, (ir.ArrayType,
                                             ir.LiteralStructType,
                                             ir.IdentifiedStructType))
              and not isinstance(storage, (ir.ArrayType,
                                           ir.LiteralStructType,
                                           ir.IdentifiedStructType))):
            from siec.codegen.sizes import target_data

            data = target_data(gen.target)
            selected_size = selected.type.get_abi_size(
                data, context=gen.module.context)
            storage_size = storage.get_abi_size(
                data, context=gen.module.context)
            if selected_size != storage_size:
                raise TypeError(
                    f"union field {field.name!r} cannot initialize static "
                    "union storage of a different size")
            if (isinstance(selected.type, ir.PointerType)
                    and isinstance(storage, ir.IntType)):
                selected_storage = selected.ptrtoint(storage)
            elif (isinstance(selected.type, ir.IntType)
                  and isinstance(storage, ir.PointerType)):
                selected_storage = selected.inttoptr(storage)
            else:
                selected_storage = selected.bitcast(storage)
        else:
            raise TypeError(
                f"union field {field.name!r} cannot initialize this static "
                "union storage")

        values = [selected_storage]
        values.extend(ir.Constant(element_type, None)
                      for element_type in type_.elements[1:])
        return ir.Constant(type_, values)

    if strip_const(sie_type).endswith("[]"):
        values = list(empty_array_value(gen, type_).constant)
    else:
        values = [
            ir.Constant(field_type, None) for field_type in type_.elements]
    for omitted in plan.omitted:
        values[omitted.index] = constant_field_default(
            gen, type_.elements[omitted.index], omitted.field)
    for element in plan.elements:
        values[element.index] = constant_value(
            gen,
            element.value,
            type_.elements[element.index],
            element.target,
        )

    return ir.Constant(type_, values)
