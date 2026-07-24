"""Emission of expressions: the dispatcher, lvalues, literals, and operators.

Type analysis lives in siec.codegen.inference, conversions in
siec.codegen.coercion, and calls in siec.codegen.calls.
"""

from llvmlite import ir

from siec.ast import (
    AggregateLiteral,
    AsmBlock,
    ArrayLiteral,
    BinaryOp,
    Block,
    BlockExpr,
    BoolLiteral,
    Call,
    Cast,
    CharLiteral,
    EnumMember,
    Expr,
    FloatLiteral,
    Index,
    IntLiteral,
    Member,
    MethodCall,
    NullLiteral,
    SizeOf,
    Slice,
    StrLiteral,
    Ternary,
    TupleLiteral,
    TypeId,
    TypeName,
    TypeOf,
    UnaryOp,
    Var,
)
from siec.codegen.asm import emit_asm_block
from siec.codegen.calls import emit_call
from siec.codegen.coercion import emit_cast, emit_coerced
from siec.codegen.enums import member_value, resolve_enum
from siec.codegen.generator import CodeGenerator, entry_alloca, make_volatile
from siec.codegen.sizes import size_of
from siec.codegen.inference import (
    ARITHMETIC,
    FLOAT_ARITHMETIC,
    UNSIGNED_ARITHMETIC,
    check_signedness,
    enum_backing,
    expr_sie_type,
    fold_qualified,
    hoist_member,
    is_float,
    member_field,
    numeric_class,
    operator_call,
    type_info,
)
from siec.codegen.types import (is_array_struct, is_reference, raw_array,
                                resolve_type, strip_const, strip_reference)


def emit_expression(gen: CodeGenerator, builder: ir.IRBuilder, expr: Expr,
                    expected_type: ir.Type | None, scope: dict):
    """
    Emit an expression, coercing literals to expected_type when given.
    """
    # dispatch on the node type; each branch returns an LLVM value
    if isinstance(expr, IntLiteral):
        # integer literals take the type of their context, defaulting to
        # i32; a float context adopts them like C's promotions do
        if is_float(expected_type):
            return ir.Constant(expected_type, float(expr.value))

        int_type = expected_type if isinstance(expected_type, ir.IntType) else ir.IntType(32)
        return ir.Constant(int_type, expr.value)

    if isinstance(expr, FloatLiteral):
        # float literals take the float type of their context, defaulting to f64
        float_type = expected_type if is_float(expected_type) else ir.DoubleType()
        return ir.Constant(float_type, expr.value)

    if isinstance(expr, StrLiteral):
        ptr = emit_string(gen, builder, expr.value)

        # a string literal fills a 'char[]' context as the fat {char*, u64}
        # value, its length excluding the null terminator
        if is_array_struct(expected_type) and expected_type.elements[0] == ptr.type:
            value = ir.Constant(expected_type, ir.Undefined)
            value = builder.insert_value(value, ptr, 0)
            return builder.insert_value(
                value, ir.Constant(ir.IntType(64), len(expr.value.encode())), 1)

        return ptr

    if isinstance(expr, BoolLiteral):
        # boolean literals are i1 constants, independent of the context type
        return ir.Constant(ir.IntType(1), 1 if expr.value else 0)

    if isinstance(expr, CharLiteral):
        # a char literal is exactly a 'char': one byte, its own type
        return ir.Constant(ir.IntType(8), expr.value.encode()[0])

    if isinstance(expr, NullLiteral):
        # 'null' adopts whatever pointer context it lands in, an opaque*
        # on its own
        if expected_type is None or isinstance(expected_type, ir.PointerType):
            return ir.Constant(expected_type or ir.PointerType(ir.IntType(8)), None)

        raise TypeError("'null' needs a pointer context")

    if isinstance(expr, EnumMember):
        # an 'S::m' whose base is no enum references the method itself
        try:
            known_enum = gen.enums.get(resolve_enum(gen, expr.enum)) is not None
        except (NameError, TypeError):
            known_enum = False

        if not known_enum:
            from siec.codegen.methods import method_reference

            if (func := method_reference(gen, expr)) is not None:
                return func

            # a struct base names its missing method precisely
            from siec.codegen.aliases import expand_alias

            try:
                named = strip_const(expand_alias(gen, expr.enum))
            except (NameError, TypeError):
                named = None

            if named in gen.structs:
                raise TypeError(f"type {expr.enum!r} has no method "
                                f"{expr.member!r}")

        # an enum member adopts an integer context like a literal would,
        # defaulting to its enum's backing type
        value = member_value(gen, expr)
        if isinstance(expected_type, ir.IntType):
            return ir.Constant(expected_type, value)

        backing = gen.enums[resolve_enum(gen, expr.enum)].backing
        return ir.Constant(resolve_type(backing), value)

    if isinstance(expr, SizeOf):
        # 'sizeof' is a compile-time constant adopting an integer context
        # like a literal, defaulting to u64
        size = size_of(gen, expr.name, scope)
        if isinstance(expected_type, ir.IntType):
            return ir.Constant(expected_type, size)

        return ir.Constant(ir.IntType(64), size)

    if isinstance(expr, TypeName):
        # '@typename' of an 'Any' looks its wrapped name up at runtime,
        # by the id; anything else bakes the name in as a string literal
        target = expr.name
        if (isinstance(target, str) and target in scope
                and strip_const(strip_reference(scope[target].type)) == "Any"):
            target = Var(target)

        if not isinstance(target, str):
            from siec.codegen.inference import infer_type

            source = infer_type(gen, target, scope)
            if source and strip_const(strip_reference(source)) == "Any":
                ident = emit_expression(gen, builder, Member(target, "id"),
                                        ir.IntType(64), scope)
                value = builder.call(typename_table(gen), [ident])

                # a 'char*' context takes the data pointer alone
                if isinstance(expected_type, ir.PointerType):
                    return builder.extract_value(value, 0, name="typename.data")

                return value

        return emit_expression(gen, builder,
                               StrLiteral(typename_of(gen, expr.name, scope)),
                               expected_type, scope)

    if isinstance(expr, TypeId):
        # '@typeid' is the name's hash, adopting an integer context like
        # a literal and defaulting to u64
        value = fnv1a(typename_of(gen, expr.name, scope))
        if isinstance(expected_type, ir.IntType) and expected_type.width >= 64:
            return ir.Constant(expected_type, value)

        return ir.Constant(ir.IntType(64), value)

    if isinstance(expr, TypeOf):
        # '@typeof' of an 'Any' reads its runtime id; any other operand
        # folds to its static type's id, the operand never evaluated
        from siec.codegen.inference import infer_type

        source = infer_type(gen, expr.value, scope)
        if source is None:
            raise TypeError("cannot take '@typeof': the expression "
                            "has no type")

        if strip_const(strip_reference(source)) == "Any":
            return emit_expression(gen, builder, Member(expr.value, "id"),
                                   expected_type, scope)

        return emit_expression(gen, builder,
                               TypeId(strip_const(strip_reference(source))),
                               expected_type, scope)

    if isinstance(expr, AsmBlock):
        return emit_asm_block(gen, builder, expr, scope)

    if isinstance(expr, AggregateLiteral):
        return emit_aggregate(gen, builder, expr, expected_type, scope)

    if isinstance(expr, BlockExpr):
        return emit_block_expr(gen, builder, expr, expected_type, scope)

    if isinstance(expr, ArrayLiteral):
        return emit_array(gen, builder, expr, expected_type, scope)

    if isinstance(expr, TupleLiteral):
        return emit_tuple(gen, builder, expr, scope)

    if isinstance(expr, Var):
        # variables load their current value from their stack slot; a
        # '@volatile' struct's loads are never elided or reordered
        if expr.name in scope:
            load = builder.load(scope[expr.name].slot, name=expr.name)
            if gen.volatile_struct(load.type):
                make_volatile(load)

            return load

        # 'f<i32>' outside a call references a generic function's
        # instance, resolved and gated by its own dotted or plain name
        if expr.type_args is not None:
            from siec.codegen.generics import emit_generic_reference

            return emit_generic_reference(gen, expr)

        # an imported module's names need their qualified spelling or a
        # member import; only what this file sees resolves unqualified
        if not expr.qualified and not gen.sees(expr.name):
            raise NameError(f"undefined variable {expr.name!r}")

        # a constant substitutes its value expression in place, coerced to
        # its annotated type when it has one, adapting like a literal otherwise
        from siec.codegen.constants import find_constant

        const = find_constant(gen, expr.name, getattr(expr, "module_file", None))
        if const is not None:
            if const.type is not None:
                return emit_coerced(gen, builder, const.value, const.type, scope)

            return emit_expression(gen, builder, const.value, expected_type, scope)

        # a bare object-like macro expands in place, C's 'errno'-style
        if expr.name in gen.macros and gen.macros[expr.name].params is None:
            return emit_call(gen, builder, Call(expr.name, []), scope)

        # a global loads its current value from its storage; the current
        # file's statics resolve first, other files' never
        symbol = gen.resolve_symbol(expr.name)
        if symbol in gen.globals:
            load = builder.load(gen.module.globals[symbol], name=expr.name)
            if gen.volatile_struct(load.type):
                make_volatile(load)

            return load

        # a bare function name is a reference to that function; an
        # overloaded one has no arguments to pick its candidate by
        from siec.codegen.overloads import overload_candidates

        if len(gen.overloads.get(symbol, ())) > 1:
            raise TypeError(f"ambiguous reference to overloaded "
                            f"function {expr.name!r}")

        func = gen.module.globals.get(overload_candidates(gen, symbol)[0])
        if isinstance(func, ir.Function):
            return func

        raise NameError(f"undefined variable {expr.name!r}")

    if isinstance(expr, Call):
        return emit_call(gen, builder, expr, scope)

    if isinstance(expr, MethodCall):
        from siec.codegen.methods import emit_method_call

        return emit_method_call(gen, builder, expr, scope)

    if isinstance(expr, Index):
        # a tuple's element reads by its constant index
        if strip_const(expr_sie_type(gen, expr.base, scope) or "").startswith("Tuple<"):
            _, index, _ = tuple_element(gen, expr, scope)
            base = emit_expression(gen, builder, expr.base, None, scope)
            return builder.extract_value(base, index, name=f"tuple.{index}")

        # a raw array's elements read through the base's address, or a
        # stack spill when it has none (a call's result, say)
        if is_raw(gen, expr.base, scope):
            index = emit_expression(gen, builder, expr.index, ir.IntType(64), scope)
            try:
                base = emit_lvalue(gen, builder, expr.base, scope)
            except (TypeError, NameError):
                value = emit_expression(gen, builder, expr.base, None, scope)
                base = entry_alloca(builder, value.type, "raw.spill")
                builder.store(value, base)

            slot = builder.gep(base, [ir.Constant(ir.IntType(32), 0), index])
            return builder.load(slot)

        # the element context implies the base's array shape, which is what
        # gives a literal base ('{ptr, n}[1]', say) its type
        base_context = None
        if expected_type is not None and not isinstance(expected_type, ir.VoidType):
            base_context = ir.LiteralStructType([ir.PointerType(expected_type),
                                                 ir.IntType(64)])

        # pointer indexing, C-style: offset the base pointer and load the
        # element; an array indexes through its data pointer
        base = emit_expression(gen, builder, expr.base, base_context, scope)
        if is_array_struct(base.type):
            base = builder.extract_value(base, 0, name="index.data")

        if not isinstance(base.type, ir.PointerType):
            raise TypeError(f"cannot index a value of type {base.type}")

        index = emit_expression(gen, builder, expr.index, ir.IntType(64), scope)
        load = builder.load(builder.gep(base, [index]))
        if gen.volatile_struct(load.type):
            make_volatile(load)

        return load

    if isinstance(expr, Slice):
        return emit_slice(gen, builder, expr, expected_type, scope)

    if isinstance(expr, Member):
        # a pure name chain may be a module's member, spelled qualified
        if (folded := fold_qualified(gen, expr, scope)) is not None:
            return emit_expression(gen, builder, folded, expected_type, scope)

        # an unnamed member's fields hoist: 'r.value' reads through 'r.#n'
        hoist_member(gen, expr, scope)

        # a raw array's 'length' is its compile-time element count,
        # adopting an integer context like a literal; a tuple's is its
        # arity, read-only the same way
        base_name = strip_const(expr_sie_type(gen, expr.base, scope))
        if raw_array(base_name) is not None and expr.field == "length":
            size = int(raw_array(base_name)[1])
            if isinstance(expected_type, ir.IntType):
                return ir.Constant(expected_type, size)

            return ir.Constant(ir.IntType(64), size)

        if (base_name or "").startswith("Tuple<") and expr.field == "length":
            from siec.codegen.generics import split_generic

            arity = len(split_generic(base_name)[1])
            if isinstance(expected_type, ir.IntType):
                return ir.Constant(expected_type, arity)

            return ir.Constant(ir.IntType(64), arity)

        # a union field reads through the union's address, reinterpreted
        info = type_info(gen, expr_sie_type(gen, expr.base, scope))
        if info is not None and info.is_union:
            return emit_union_member(gen, builder, expr, info, scope)

        # read a struct or array field: extract it from the base value by index
        index = member_field(gen, expr, scope)[0]
        base = emit_expression(gen, builder, expr.base, None, scope)
        return builder.extract_value(base, index, name=expr.field)

    if isinstance(expr, Cast):
        return emit_cast(gen, builder, expr, scope)

    if isinstance(expr, UnaryOp):
        # unary minus negates in the context type; '~' flips bits; 'not' inverts a bool
        if expr.op == "-":
            value = emit_expression(gen, builder, expr.operand, expected_type, scope)
            return builder.fneg(value) if is_float(value.type) else builder.neg(value)

        if expr.op == "~":
            value = emit_expression(gen, builder, expr.operand, expected_type, scope)
            if is_float(value.type):
                raise TypeError("cannot apply '~' to a float operand")

            return builder.not_(value)

        if expr.op == "not":
            return builder.not_(emit_bool(gen, builder, expr.operand, scope))

        # '&' takes the address of an assignable expression: its stack slot;
        # a reference parameter is not dereferenceable - no address rooted
        # at it may be taken ('&s' and '&s.member' would both leak the
        # caller's storage)
        if expr.op == "&":
            root = expr.operand
            while True:
                if isinstance(root, (Member, Index)):
                    root = root.base
                elif isinstance(root, UnaryOp) and root.op == "*":
                    root = root.operand
                else:
                    break

            if (isinstance(root, Var) and root.name in scope
                    and is_reference(scope[root.name].type)):
                through = "of" if root is expr.operand else "through"
                raise TypeError(f"cannot take an address {through} reference "
                                f"parameter {root.name!r}")

            return emit_lvalue(gen, builder, expr.operand, scope)

        # '*' dereferences a pointer, reading the element it points at:
        # 'p[0]' by another spelling, sharing indexing's semantics
        if expr.op == "*":
            return emit_expression(gen, builder, Index(expr.operand, IntLiteral(0)),
                                   expected_type, scope)

        raise TypeError(f"unknown unary operator {expr.op!r}")

    if isinstance(expr, Ternary):
        return emit_ternary(gen, builder, expr, expected_type, scope)

    if isinstance(expr, BinaryOp):
        # comparing '@typeof' against a bare type name means its id:
        # '@typeof(v) == T' is '@typeof(v) == @typeid(T)'
        if expr.op in ("==", "!=") and (isinstance(expr.left, TypeOf)
                                        or isinstance(expr.right, TypeOf)):
            expr.left = type_operand(gen, expr.left, scope)
            expr.right = type_operand(gen, expr.right, scope)

        # a struct operand's operator is the method call it desugars to:
        # 'a + b' is 'a.add(b)', each overload picked by b's type, and
        # 'a != b' the negated 'not a.eq(b)'
        if (rewritten := operator_call(gen, expr, scope)) is not None:
            return emit_expression(gen, builder, rewritten, expected_type, scope)

        # logical operators coerce each side to a bool on its own terms
        if expr.op in ("and", "or"):
            return emit_logical(gen, builder, expr, scope)

        # every other operator combines the operand values directly and
        # emits unsigned instructions when the operands are unsigned
        unsigned = check_signedness(gen, expr, scope) == "unsigned"

        if expr.op in ARITHMETIC:
            # arithmetic and bitwise: both sides share the context type; the result keeps it
            left = emit_expression(gen, builder, expr.left, expected_type, scope)
            right = emit_expression(gen, builder, expr.right, left.type, scope)
            left, right = match_widths(gen, builder, expr, left, right, unsigned, scope)

            # float operands take the float instructions; bitwise has none
            if is_float(left.type):
                if expr.op not in FLOAT_ARITHMETIC:
                    raise TypeError(f"cannot apply {expr.op!r} to float operands")

                return getattr(builder, FLOAT_ARITHMETIC[expr.op])(left, right)

            method = UNSIGNED_ARITHMETIC[expr.op] if (
                unsigned and expr.op in UNSIGNED_ARITHMETIC) else ARITHMETIC[expr.op]

            return getattr(builder, method)(left, right)

        if expr.op == "**":
            return emit_power(gen, builder, expr, expected_type, scope, unsigned)

        # comparisons: type the right side by the left, yield an i1
        left = emit_expression(gen, builder, expr.left, None, scope)
        right = emit_expression(gen, builder, expr.right, left.type, scope)
        left, right = match_widths(gen, builder, expr, left, right, unsigned, scope)

        if is_float(left.type):
            return builder.fcmp_ordered(expr.op, left, right)

        # only scalars and pointers compare; structs and arrays do not
        if not isinstance(left.type, (ir.IntType, ir.PointerType)):
            raise TypeError(f"cannot apply {expr.op!r} to a value of type "
                            f"{expr_sie_type(gen, expr.left, scope) or left.type}")

        compare = builder.icmp_unsigned if unsigned else builder.icmp_signed
        return compare(expr.op, left, right)

    raise TypeError(f"cannot generate code for {expr!r}")


def emit_lvalue(gen: CodeGenerator, builder: ir.IRBuilder, expr: Expr, scope: dict):
    """
    Emit the address of an assignable expression: a variable, a struct/array
    field, a pointer-indexed element, or a dereferenced pointer.
    """
    # a macro use is the place its expansion names, in the macro's view
    from siec.codegen.macros import macro_place, macro_view

    if (place := macro_place(gen, expr, scope)) is not None:
        name, expansion = place
        if isinstance(expansion, (Block, BlockExpr)):
            raise TypeError(f"macro {name!r} does not expand to an "
                            "assignable place")

        with macro_view(gen, name):
            return emit_lvalue(gen, builder, expansion, scope)

    if isinstance(expr, Var):
        if expr.name in scope:
            return scope[expr.name].slot

        # a global's slot is its module-level storage, if this file sees it
        symbol = gen.resolve_symbol(expr.name)
        if (expr.qualified or gen.sees(expr.name)) and symbol in gen.globals:
            return gen.module.globals[symbol]

        raise NameError(f"undefined variable {expr.name!r}")

    # a '&T'-returning call's value IS an address: assignable storage
    if isinstance(expr, Call):
        return emit_call(gen, builder, expr, scope, as_address=True)

    if isinstance(expr, MethodCall):
        from siec.codegen.methods import emit_method_call

        return emit_method_call(gen, builder, expr, scope, as_address=True)

    if isinstance(expr, Member):
        # a pure name chain may be a module's member, spelled qualified
        if (folded := fold_qualified(gen, expr, scope)) is not None:
            return emit_lvalue(gen, builder, folded, scope)

        # an unnamed member's fields hoist: 'r.value' writes through 'r.#n'
        hoist_member(gen, expr, scope)

        index, field_name = member_field(gen, expr, scope)
        base = emit_lvalue(gen, builder, expr.base, scope)

        # a union field reinterprets the shared storage: the union's own
        # address, read as the field's type
        info = type_info(gen, expr_sie_type(gen, expr.base, scope))
        if info is not None and info.is_union:
            field_type = resolve_type(field_name, gen.structs)
            return builder.bitcast(base, ir.PointerType(field_type), name=expr.field)

        # index into the base's address: gep past the aggregate to the field slot
        return builder.gep(base, [ir.Constant(ir.IntType(32), 0),
                                  ir.Constant(ir.IntType(32), index)], name=expr.field)

    if isinstance(expr, Index):
        # a tuple's element sits inline: its constant index slots into
        # the base's address
        if strip_const(expr_sie_type(gen, expr.base, scope) or "").startswith("Tuple<"):
            _, index, _ = tuple_element(gen, expr, scope)
            base = emit_lvalue(gen, builder, expr.base, scope)
            return builder.gep(base, [ir.Constant(ir.IntType(32), 0),
                                      ir.Constant(ir.IntType(32), index)],
                               name=f"tuple.{index}")

        # a raw array's elements sit inline: index into the base's address
        if is_raw(gen, expr.base, scope):
            base = emit_lvalue(gen, builder, expr.base, scope)
            index = emit_expression(gen, builder, expr.index, ir.IntType(64), scope)
            return builder.gep(base, [ir.Constant(ir.IntType(32), 0), index])

        # offset the base pointer's value to the element's address, C-style;
        # an array's elements are addressed through its data pointer
        base = emit_expression(gen, builder, expr.base, None, scope)
        if is_array_struct(base.type):
            base = builder.extract_value(base, 0, name="index.data")

        if not isinstance(base.type, ir.PointerType):
            raise TypeError(f"cannot index a value of type {base.type}")

        index = emit_expression(gen, builder, expr.index, ir.IntType(64), scope)
        return builder.gep(base, [index])

    # a dereference names the storage its pointer points at, addressed as
    # its 'p[0]' spelling would be
    if isinstance(expr, UnaryOp) and expr.op == "*":
        return emit_lvalue(gen, builder, Index(expr.operand, IntLiteral(0)), scope)

    raise TypeError(f"expression is not assignable: {expr!r}")


def is_raw(gen: CodeGenerator, expr: Expr, scope: dict) -> bool:
    """
    Whether an expression's Sie type is a raw array, behind any 'const'.
    """
    name = strip_const(expr_sie_type(gen, expr, scope))
    return (raw := raw_array(name)) is not None and not raw[2]


def emit_bool(gen: CodeGenerator, builder: ir.IRBuilder, expr: Expr, scope: dict):
    """
    Emit an expression coerced to a bool: numbers compare against zero,
    C-style, and pointers against null.
    """
    value = emit_expression(gen, builder, expr, ir.IntType(1), scope)

    if isinstance(value.type, ir.PointerType):
        return builder.icmp_unsigned("!=", value, ir.Constant(value.type, None))

    if is_float(value.type):
        return builder.fcmp_ordered("!=", value, ir.Constant(value.type, 0))

    # only scalars compare against zero; a struct or array has no truth
    if not isinstance(value.type, ir.IntType):
        raise TypeError(f"cannot test a value of type "
                        f"{expr_sie_type(gen, expr, scope) or value.type} "
                        "for truth")

    if value.type != ir.IntType(1):
        value = builder.icmp_signed("!=", value, ir.Constant(value.type, 0))

    return value


def emit_union_member(gen: CodeGenerator, builder: ir.IRBuilder, expr: Member,
                      info, scope: dict):
    """
    Read a union field: through the union's address when it has one, or a
    stack spill of its value otherwise, reinterpreted as the field's type.
    """
    field_type = resolve_type(member_field(gen, expr, scope)[1], gen.structs)

    try:
        address = emit_lvalue(gen, builder, expr, scope)
    except (TypeError, NameError):
        # an unaddressable union (a call's result, say) reads via a spill
        base = emit_expression(gen, builder, expr.base, None, scope)
        spill = entry_alloca(builder, base.type, "union.spill")
        builder.store(base, spill)
        address = builder.bitcast(spill, ir.PointerType(field_type), name=expr.field)

    load = builder.load(address, name=expr.field)
    if info.volatile:
        make_volatile(load)

    return load


def match_widths(gen: CodeGenerator, builder: ir.IRBuilder, expr: BinaryOp,
                 left: ir.Value, right: ir.Value, unsigned: bool, scope: dict):
    """
    Widen the narrower of two mismatched numeric operands to the other's
    type: the same-prefix widening an assignment would apply. Signedness
    mismatches were already rejected; a declared non-numeric type (a char,
    a bool) has no widening to lean on and is an error instead.
    """
    # mixed float widths extend the narrower side
    if is_float(left.type) and is_float(right.type) and left.type != right.type:
        if isinstance(left.type, ir.FloatType):
            return builder.fpext(left, right.type), right

        return left, builder.fpext(right, left.type)

    if (not isinstance(left.type, ir.IntType) or not isinstance(right.type, ir.IntType)
            or left.type.width == right.type.width):
        return left, right

    for operand in (expr.left, expr.right):
        name = enum_backing(gen, expr_sie_type(gen, operand, scope))
        if name is not None and numeric_class(name) is None:
            raise TypeError(f"cannot apply {expr.op!r} to a {name!r} operand "
                            "of a different width")

    extend = builder.zext if unsigned else builder.sext
    if left.type.width < right.type.width:
        return extend(left, right.type), right

    return left, extend(right, left.type)


def emit_power(gen: CodeGenerator, builder: ir.IRBuilder, expr: BinaryOp,
               expected_type: ir.Type | None, scope: dict, unsigned: bool = False):
    """
    Emit '**' as a multiply loop, since LLVM has no integer power instruction.
    """
    base = emit_expression(gen, builder, expr.left, expected_type, scope)
    if is_float(base.type):
        raise TypeError("cannot apply '**' to float operands")

    exp = emit_expression(gen, builder, expr.right, base.type, scope)

    # the result and the remaining exponent live in slots driven by the loop
    result = entry_alloca(builder, base.type, "pow.result")
    remaining = entry_alloca(builder, exp.type, "pow.exp")
    builder.store(ir.Constant(base.type, 1), result)
    builder.store(exp, remaining)

    func = builder.function
    cond_block = func.append_basic_block("pow.cond")
    body_block = func.append_basic_block("pow.body")
    end_block = func.append_basic_block("pow.end")

    builder.branch(cond_block)

    # keep multiplying while the exponent is positive
    builder.position_at_end(cond_block)
    count = builder.load(remaining)

    compare = builder.icmp_unsigned if unsigned else builder.icmp_signed
    builder.cbranch(compare(">", count, ir.Constant(exp.type, 0)),
                    body_block, end_block)

    builder.position_at_end(body_block)
    builder.store(builder.mul(builder.load(result), base), result)
    builder.store(builder.sub(count, ir.Constant(exp.type, 1)), remaining)
    builder.branch(cond_block)

    builder.position_at_end(end_block)
    return builder.load(result)


def emit_ternary(gen: CodeGenerator, builder: ir.IRBuilder, expr: Ternary,
                 expected_type: ir.Type | None, scope: dict):
    """
    Emit 'cond ? then : orelse' as a branch joined by a phi: only the
    chosen arm is evaluated, and both must agree on their type.
    """
    cond = emit_bool(gen, builder, expr.condition, scope)

    func = builder.function
    then_block = func.append_basic_block("ternary.then")
    else_block = func.append_basic_block("ternary.else")
    end_block = func.append_basic_block("ternary.end")

    builder.cbranch(cond, then_block, else_block)

    # each arm may open blocks of its own; the phi needs its exit block
    builder.position_at_end(then_block)
    then_value = emit_expression(gen, builder, expr.then, expected_type, scope)
    then_exit = builder.block
    builder.branch(end_block)

    # without a context, the else arm adopts the then arm's type, so
    # literals on either side adapt to the declared one
    builder.position_at_end(else_block)
    else_value = emit_expression(gen, builder, expr.orelse,
                                 expected_type or then_value.type, scope)
    else_exit = builder.block
    builder.branch(end_block)

    if then_value.type != else_value.type:
        raise TypeError(f"ternary arms disagree: {then_value.type} vs {else_value.type}")

    builder.position_at_end(end_block)
    result = builder.phi(then_value.type, name="ternary")
    result.add_incoming(then_value, then_exit)
    result.add_incoming(else_value, else_exit)
    return result


def emit_logical(gen: CodeGenerator, builder: ir.IRBuilder, expr: BinaryOp, scope: dict):
    """
    Emit 'and'/'or' with short-circuit evaluation, yielding a bool.
    """
    left = emit_bool(gen, builder, expr.left, scope)

    func = builder.function
    rhs_block = func.append_basic_block(f"{expr.op}.rhs")
    end_block = func.append_basic_block(f"{expr.op}.end")

    # 'and' only evaluates the right side when the left is true; 'or' when false
    left_block = builder.block
    if expr.op == "and":
        builder.cbranch(left, rhs_block, end_block)
    else:
        builder.cbranch(left, end_block, rhs_block)

    builder.position_at_end(rhs_block)
    right = emit_bool(gen, builder, expr.right, scope)

    # the right side may have opened blocks of its own; the phi needs its exit block
    rhs_exit = builder.block
    builder.branch(end_block)

    # when short-circuited, the result is the left value that decided the branch
    builder.position_at_end(end_block)
    result = builder.phi(ir.IntType(1))
    result.add_incoming(ir.Constant(ir.IntType(1), expr.op == "or"), left_block)
    result.add_incoming(right, rhs_exit)
    return result


def emit_aggregate(gen: CodeGenerator, builder: ir.IRBuilder, expr: AggregateLiteral,
                   expected_type: ir.Type | None, scope: dict, field_names: list | None = None):
    """
    Emit an aggregate literal, filling the expected struct or array type field by field.

    A positional literal fills every field in order; a named one fills any
    subset in any order, leaving the rest zero-initialized. When the field
    Sie type names are known, each element is coerced to its field's type
    with the same widening rules as any other typed context.
    """
    # the literal takes its shape from context: an array's '{ptr, length}', say
    if not isinstance(expected_type, (ir.LiteralStructType, ir.IdentifiedStructType)):
        raise TypeError(f"aggregate literal needs a struct or array type, not {expected_type}")

    # a union's fields share storage; no literal fills them field by field
    if (isinstance(expected_type, ir.IdentifiedStructType)
            and (info := gen.structs.get(expected_type.name)) is not None
            and info.is_union):
        raise TypeError("a union takes no aggregate literal; assign one "
                        "of its fields instead")

    field_types = expected_type.elements

    if expr.names is not None:
        return emit_named_aggregate(gen, builder, expr, expected_type, scope, field_names)

    if len(expr.elements) != len(field_types):
        raise TypeError(f"aggregate literal has {len(expr.elements)} elements, "
                        f"expected {len(field_types)}")

    # build the value by inserting each element into an initially-undefined aggregate
    value = ir.Constant(expected_type, ir.Undefined)
    for index, (element, field_type) in enumerate(zip(expr.elements, field_types)):
        if field_names is not None:
            field = emit_coerced(gen, builder, element, field_names[index], scope)
        else:
            field = emit_expression(gen, builder, element, field_type, scope)

        value = builder.insert_value(value, field, index)

    return value


def aggregate_fields(gen: CodeGenerator, type_: ir.Type) -> list[str] | None:
    """
    The field names of an aggregate LLVM type, in order: a registered
    struct's own, or an array's synthetic 'data' and 'length'.
    """
    if isinstance(type_, ir.IdentifiedStructType):
        info = gen.structs.get(type_.name)
        if info is not None and info.fields:
            return [field.name for field in info.fields]

    if is_array_struct(type_):
        return ["data", "length"]

    return None


def emit_named_aggregate(gen: CodeGenerator, builder: ir.IRBuilder, expr: AggregateLiteral,
                         expected_type: ir.Type, scope: dict, field_names: list | None):
    """
    Emit a named aggregate literal over a zero-initialized base: each 'x =
    v' fills its field wherever it sits, and untouched fields take their
    declared defaults, staying zero without one.
    """
    names = aggregate_fields(gen, expected_type)
    if names is None:
        raise TypeError(f"named aggregate literal needs a struct or array "
                        f"type with known fields, not {expected_type}")

    index_of = {name: index for index, name in enumerate(names)}

    value = ir.Constant(expected_type, None)
    seen = set()
    for name, element in zip(expr.names, expr.elements):
        if name not in index_of:
            raise TypeError(f"aggregate literal names unknown field {name!r}")

        if name in seen:
            raise TypeError(f"aggregate literal sets field {name!r} more than once")

        seen.add(name)
        index = index_of[name]
        if field_names is not None:
            field = emit_coerced(gen, builder, element, field_names[index], scope)
        else:
            field = emit_expression(gen, builder, element,
                                    expected_type.elements[index], scope)

        value = builder.insert_value(value, field, index)

    if (isinstance(expected_type, ir.IdentifiedStructType)
            and (info := gen.structs.get(expected_type.name)) is not None
            and info.fields is not None):
        for index, field in enumerate(info.fields):
            if field.name not in seen:
                filled = field_default(gen, builder, field)
                if filled is not None:
                    value = builder.insert_value(value, filled, index)

    return value


def field_default(gen: CodeGenerator, builder: ir.IRBuilder, field):
    """
    The value an unfilled field takes: its declared default, or its
    struct type's own defaults, or None to stay zero.

    A default is written in the struct's declaration, away from any local
    names, so it emits against an empty scope.
    """
    if field.default is not None:
        return emit_coerced(gen, builder, field.default, field.type, {})

    return default_value(gen, builder, field.type)


def default_value(gen: CodeGenerator, builder: ir.IRBuilder, type_name: str | None):
    """
    A struct type's default aggregate: each defaulted field filled, in
    nested structs too, the rest zero; None when nothing declares one,
    leaving a bare declaration uninitialized as ever.
    """
    info = gen.structs.get(strip_const(type_name) if type_name else None)
    if info is None or info.fields is None or info.is_union:
        return None

    value, any_default = ir.Constant(info.type, None), False
    for index, field in enumerate(info.fields):
        filled = field_default(gen, builder, field)
        if filled is not None:
            value = builder.insert_value(value, filled, index)
            any_default = True

    return value if any_default else None


def emit_tuple(gen: CodeGenerator, builder: ir.IRBuilder, expr: TupleLiteral,
               scope: dict, target_name: str | None = None):
    """
    Emit a tuple literal '(a, b, ...)': a 'Tuple<A, B, ...>' value, its
    element types from the target when one is given, inferred from the
    elements otherwise.
    """
    from siec.codegen.aliases import expand_alias
    from siec.codegen.generics import split_generic
    from siec.codegen.inference import infer_type

    if target_name is not None:
        base, args = split_generic(strip_const(target_name)) or (None, [])
        if base != "Tuple":
            raise TypeError(f"a tuple literal needs a Tuple target, "
                            f"not {target_name!r}")

        if len(args) != len(expr.elements):
            take = len(args)
            raise TypeError(f"tuple literal has {len(expr.elements)} "
                            f"element{'s' if len(expr.elements) != 1 else ''}; "
                            f"{strip_const(target_name)!r} takes {take}")
    else:
        args = [infer_type(gen, element, scope) for element in expr.elements]
        if not all(args):
            raise TypeError("cannot infer a tuple element's type: "
                            "annotate the tuple")

    canonical = expand_alias(gen, f"Tuple<{','.join(args)}>", checked=False)
    args = split_generic(canonical)[1]

    value = ir.Constant(resolve_type(canonical, gen.structs), None)
    for i, element in enumerate(expr.elements):
        filled = emit_coerced(gen, builder, element, args[i], scope)
        value = builder.insert_value(value, filled, i, name=f"tuple.{i}")

    return value


def type_operand(gen: CodeGenerator, expr: Expr, scope: dict) -> Expr:
    """
    Rewrite a bare type name compared against '@typeof' into its
    '@typeid': a Var naming a type (and shadowed by no variable) means
    the type's id. Anything else passes through untouched.
    """
    from siec.codegen.aliases import expand_alias

    if not isinstance(expr, Var) or expr.name in scope:
        return expr

    spelling = expr.name
    if expr.type_args is not None:
        spelling += f"<{','.join(expr.type_args)}>"

    try:
        resolve_type(expand_alias(gen, spelling), gen.structs)
    except (TypeError, NameError):
        return expr

    return TypeId(spelling)


def typename_table(gen: CodeGenerator):
    """
    The runtime 'id -> name' lookup function '@typename' calls on an
    Any: declared on first use, its body built once every wrap site has
    been seen.
    """
    if gen.typename_fn is None:
        fn_type = ir.FunctionType(resolve_type("char[]", gen.structs),
                                  [ir.IntType(64)])
        gen.typename_fn = ir.Function(gen.module, fn_type, "sie.typename")
        gen.typename_fn.linkage = "private"

    return gen.typename_fn


def finish_typename_table(gen: CodeGenerator) -> None:
    """
    Build the '@typename' lookup's body: a switch over every id wrapped
    anywhere in the program, an unknown id answering "?".
    """
    func = gen.typename_fn
    if func is None:
        return

    builder = ir.IRBuilder(func.append_basic_block("entry"))

    def answer(block, text):
        builder.position_at_end(block)
        data = emit_string(gen, builder, text)
        value = ir.Constant(resolve_type("char[]", gen.structs), None)
        value = builder.insert_value(value, data, 0)
        value = builder.insert_value(
            value, ir.Constant(ir.IntType(64), len(text.encode())), 1)
        builder.ret(value)

    default = func.append_basic_block("unknown")
    switch = builder.switch(func.args[0], default)

    for ident, name in gen.any_names.items():
        block = func.append_basic_block(f"id.{ident}")
        switch.add_case(ir.Constant(ir.IntType(64), ident), block)
        answer(block, name)

    answer(default, "?")


def fnv1a(text: str) -> int:
    """
    The 64-bit FNV-1a hash of a string, '@typeid's identity function.
    """
    value = 0xcbf29ce484222325
    for byte in text.encode():
        value = ((value ^ byte) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF

    return value


def typename_of(gen: CodeGenerator, name, scope: dict) -> str:
    """
    The canonical type name '@typename' resolves: a scope variable's
    declared type (a '&T' parameter naming its T), a global's, the
    written type expanded through its aliases, or an expression's
    static type.
    """
    from siec.codegen.aliases import expand_alias
    from siec.codegen.inference import infer_type

    # an expression carries its static type; the operand never emits
    if not isinstance(name, str):
        source = infer_type(gen, name, scope)
        if source is None:
            raise TypeError("cannot take the type name: the expression "
                            "has no type")

        return strip_const(strip_reference(source))

    if name in scope:
        return strip_reference(scope[name].type)

    if (symbol := gen.resolve_symbol(name)) in gen.globals:
        return gen.globals[symbol]

    expanded = expand_alias(gen, name)

    # naming nothing at all is an error, not a string
    resolve_type(expanded, gen.structs)
    return expanded


def tuple_element(gen: CodeGenerator, expr: Index, scope: dict) -> tuple:
    """
    Resolve a tuple index: the base's canonical type, the constant
    element index, and the element type names.
    """
    from siec.codegen.enums import evaluate
    from siec.codegen.generics import split_generic

    base_name = strip_const(expr_sie_type(gen, expr.base, scope))
    args = split_generic(base_name)[1]

    try:
        index = evaluate(gen, expr.index)
    except (TypeError, NameError):
        raise TypeError("a tuple index must be a compile-time "
                        "constant") from None

    if not 0 <= index < len(args):
        raise TypeError(f"tuple index {index} is out of range "
                        f"for {base_name!r}")

    return base_name, index, args


def emit_array(gen: CodeGenerator, builder: ir.IRBuilder, expr: ArrayLiteral,
              expected_type: ir.Type | None, scope: dict, element_name: str | None = None):
    """
    Emit an array literal, storing its elements into a backing array and
    wrapping a pointer to it with their count in the fat '{X*, u64}' array value.

    When the element's Sie type name is known, each element is coerced to it
    with the same widening rules as any other typed context.
    """
    # the literal takes its element type from context: an 'i32[]' target's
    # first field is a pointer to the element type it must build
    if not is_array_struct(expected_type):
        raise TypeError(f"array literal needs an array type, not {expected_type}")

    element_type = expected_type.elements[0].pointee

    # store each element into a stack-allocated backing array
    backing = entry_alloca(builder, ir.ArrayType(element_type, len(expr.elements)), "arr.lit")
    for index, element in enumerate(expr.elements):
        if element_name is not None:
            value = emit_coerced(gen, builder, element, element_name, scope)
        else:
            value = emit_expression(gen, builder, element, element_type, scope)

        slot = builder.gep(backing, [ir.Constant(ir.IntType(32), 0),
                                     ir.Constant(ir.IntType(32), index)])
        builder.store(value, slot)

    # decay the backing array to a pointer at its first element, and pair it
    # with the element count in the fat array value
    data = builder.gep(backing, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                       name="arr.lit.data")

    value = ir.Constant(expected_type, ir.Undefined)
    value = builder.insert_value(value, data, 0)
    value = builder.insert_value(value, ir.Constant(ir.IntType(64), len(expr.elements)), 1)
    return value


def emit_block_expr(gen: CodeGenerator, builder: ir.IRBuilder, expr: BlockExpr,
                    expected_type: ir.Type | None, scope: dict, target_name: str | None = None):
    """
    Emit a block used as a value: its statements run in a child scope, and
    an 'emit' inside stores the block's value and jumps past it.
    """
    # deferred import: statements and expressions are mutually recursive
    from siec.codegen.statements import emit_block

    if expected_type is None or isinstance(expected_type, ir.VoidType):
        raise TypeError("block expression needs a typed context to take its value from")

    slot = entry_alloca(builder, expected_type, "block.value")
    end_block = builder.function.append_basic_block("block.end")

    # the innermost target is what an 'emit' inside the body resolves to;
    # the defer depth marks which scopes an 'emit' leaves and must flush
    gen.emit_targets.append((slot, end_block, target_name, len(gen.defer_frames)))
    emit_block(gen, builder, expr.body, dict(scope))
    gen.emit_targets.pop()

    # every path must leave the block through an 'emit' (or a return)
    if not builder.block.is_terminated:
        raise TypeError("block expression must produce its value with 'emit'")

    builder.position_at_end(end_block)
    return builder.load(slot)


def emit_slice(gen: CodeGenerator, builder: ir.IRBuilder, expr: Slice,
               expected_type: ir.Type | None, scope: dict):
    """
    Emit an 'arr[from:to]' slice: a view over the base array's backing data,
    from 'from' (default 0) to 'to' (default the array's length).

    A slice keeps its base's type, so the context's expected type passes
    through - it's what gives a literal base its shape.
    """
    base = emit_expression(gen, builder, expr.base, expected_type, scope)
    if not is_array_struct(base.type):
        raise TypeError(f"cannot slice a value of type {base.type}")

    # the bounds are u64 element counts, like the array's own length
    start = (
        emit_coerced(gen, builder, expr.start, "u64", scope)
        if expr.start is not None
        else ir.Constant(ir.IntType(64), 0)
    )

    stop = (
        emit_coerced(gen, builder, expr.stop, "u64", scope)
        if expr.stop is not None
        else builder.extract_value(base, 1, name="slice.len")
    )

    # the view shares the backing data, offset to 'from', spanning 'to' - 'from'
    data = builder.gep(builder.extract_value(base, 0, name="slice.data"), [start])

    value = ir.Constant(base.type, ir.Undefined)
    value = builder.insert_value(value, data, 0)
    return builder.insert_value(value, builder.sub(stop, start), 1)


def emit_string(gen: CodeGenerator, builder: ir.IRBuilder, value: str):
    """
    Emit a string literal as a private global constant and return it as char*.
    """
    # null-terminate the bytes and size the constant array to fit
    data = value.encode() + b"\0"
    array_type = ir.ArrayType(ir.IntType(8), len(data))

    # store the data as a uniquely named module-level constant
    const = ir.GlobalVariable(gen.module, array_type, name=f".str.{gen.str_count}")
    const.global_constant = True
    const.linkage = "private"
    const.initializer = ir.Constant(array_type, bytearray(data))

    # hand it back decayed from [N x i8]* to a plain char*
    gen.str_count += 1
    return builder.bitcast(const, ir.PointerType(ir.IntType(8)))
