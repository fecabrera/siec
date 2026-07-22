"""Type analysis of expressions: Sie types, signedness, and numeric classes.

Everything here answers questions about expressions - what type is this,
how does it classify - without emitting any IR.
"""

from llvmlite import ir

from siec.ast import (
    AsmBlock,
    BinaryOp,
    BoolLiteral,
    Call,
    Cast,
    CharLiteral,
    EnumMember,
    Expr,
    Field,
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
    UnaryOp,
    Var,
)
from siec.codegen.aliases import expand_alias
from siec.codegen.generator import CodeGenerator, StructInfo
from siec.codegen.types import (
    fn_type_parts,
    is_aliasing,
    is_const,
    raw_array,
    resolve_type,
    sized_array,
    strip_const,
    strip_reference,
    type_signedness,
)

# arithmetic and bitwise operators and the IRBuilder method emitting each;
# division, remainder, and right shift change instruction on unsigned
# operands, and arithmetic changes wholesale on floats
ARITHMETIC = {"+": "add", "-": "sub", "*": "mul", "/": "sdiv", "%": "srem",
              "<<": "shl", ">>": "ashr", "&": "and_", "|": "or_", "^": "xor"}
UNSIGNED_ARITHMETIC = {"/": "udiv", "%": "urem", ">>": "lshr"}
FLOAT_ARITHMETIC = {"+": "fadd", "-": "fsub", "*": "fmul", "/": "fdiv", "%": "frem"}

COMPARISONS = {"<", ">", "<=", ">=", "==", "!="}

# the method a struct operand's binary operator desugars to: 'a + b' is
# 'a.add(b)', the operator interfaces' ('Add<S, T>', ...) shorthand
OPERATOR_METHODS = {"+": "add", "-": "sub", "*": "mul", "/": "div", "%": "rem"}


def operator_call(gen: "CodeGenerator", expr: BinaryOp, scope: dict) -> MethodCall | None:
    """
    Rewrite a binary operator over a struct operand into the method call
    it means: 'a + b' is 'a.add(b)', each overload picked by b's type.
    None for any other operand, whose operators keep their instructions.
    """
    method = OPERATOR_METHODS.get(expr.op)
    if method is None:
        return None

    # enum-typed operands keep their integer arithmetic
    name = strip_const(expr_sie_type(gen, expr.left, scope) or "")
    if name not in gen.structs or name in gen.enums:
        return None

    return MethodCall(expr.left, method, [expr.right])


def is_float(type_: ir.Type) -> bool:
    """
    Whether an LLVM type is a floating-point scalar.
    """
    return isinstance(type_, (ir.FloatType, ir.DoubleType))


def expr_sie_type(gen: CodeGenerator, expr: Expr, scope: dict) -> str | None:
    """
    Infer the Sie type name of an expression; None when it has no fixed one.
    """
    # variables and calls carry their declared Sie type; a bare function
    # name carries the canonical fn type of its signature; a '&T'
    # reference parameter reads as the T it aliases
    if isinstance(expr, Var):
        if expr.name in scope:
            return strip_reference(scope[expr.name].type)

        # 'f<i32>' outside a call has its instance's function type,
        # resolved and gated by its own dotted or plain name
        if expr.type_args is not None:
            from siec.codegen.generics import reference_type

            return reference_type(gen, expr)

        # only names this file sees resolve unqualified
        if not expr.qualified and not gen.sees(expr.name):
            return None

        # a constant carries its annotation; unannotated, it adapts like
        # its value expression written in place
        const = gen.constants.get(expr.name)
        if const is not None:
            return const.type if const.type is not None else expr_sie_type(
                gen, const.value, scope)

        # a global carries its declared type
        symbol = gen.resolve_symbol(expr.name)
        if symbol in gen.globals:
            return gen.globals[symbol]

        if symbol in gen.param_types:
            params = ",".join(gen.param_types[symbol])
            ret = gen.return_types.get(symbol)
            return f"fn({params})" + (f"->{ret}" if ret else "")

        return None

    # a method call on a receiver expression types like the qualified
    # call it resolves to, the receiver joining the arguments
    if isinstance(expr, MethodCall):
        from siec.codegen.methods import resolve_method, takes_receiver

        symbol = resolve_method(gen, expr_sie_type(gen, expr.receiver, scope),
                                expr.method)
        if symbol is None:
            return None

        args = ([expr.receiver, *expr.args] if takes_receiver(gen, symbol)
                else expr.args)
        return expr_sie_type(gen, Call(symbol, args, expr.type_args), scope)

    if isinstance(expr, Call):
        # a call through a function reference yields the reference's return
        # type, a '&T' return reading as the T it aliases
        if expr.name in scope and strip_const(scope[expr.name].type).startswith("fn("):
            return strip_reference(fn_type_parts(strip_const(scope[expr.name].type))[1])

        # the builtin 'enumerate(x)' types as its '__enumerate' instance
        if expr.name == "enumerate":
            from siec.codegen.methods import rewrite_enumerate

            if (rewritten := rewrite_enumerate(gen, expr, scope)) is not None:
                return expr_sie_type(gen, rewritten, scope)

        call = expr
        if "::" in expr.name:
            # 'S::m(s)' names a method through its receiver type
            from siec.codegen.methods import qualified_method

            symbol = qualified_method(gen, expr.name)
        else:
            # a name carrying '<' is a resolved instance the compiler
            # wrote; no file's view gates it
            if ("." not in expr.name and "<" not in expr.name
                    and not gen.sees(expr.name)):
                return None

            symbol = gen.resolve_callee(expr.name)
            if symbol in gen.globals and strip_const(gen.globals[symbol]).startswith("fn("):
                return strip_reference(fn_type_parts(strip_const(gen.globals[symbol]))[1])

            # a dotted callee may be a method on its receiver chain, its
            # receiver joining the arguments for inference
            if symbol is None or symbol not in gen.return_types:
                from siec.codegen.methods import method_call

                if "." in expr.name and (found := method_call(gen, expr, scope)):
                    symbol, receiver = found
                    if receiver is not None:
                        call = Call(expr.name, [receiver, *expr.args],
                                    expr.type_args)

        # an overloaded name's return type follows the candidate its
        # arguments pick; a fit bypasses a generic template sharing the
        # name, while a call no concrete candidate takes falls through
        # to it, or has no type yet
        picked = False
        if symbol in gen.overloads:
            from siec.codegen.overloads import pick_overload

            try:
                symbol = pick_overload(gen, symbol, call.args, scope)
                picked = True
            except TypeError:
                if gen.generic_functions.get(symbol) is None:
                    return None

        # a generic call's return type comes from its resolved arguments,
        # without instantiating; an unresolvable call has no type yet
        if not picked and gen.generic_functions.get(symbol) is not None:
            from siec.codegen.generics import pick_generic_call, substitute

            try:
                template, type_args = pick_generic_call(
                    gen, symbol, call, scope,
                    getattr(expr, "expected_type", None))
            except TypeError:
                return None

            if template.return_type is None:
                return None

            mapping = dict(zip(template.type_params, type_args))
            return strip_reference(
                expand_alias(gen, substitute(template.return_type, mapping)))

        # 'S(...)' constructs and types as the S it builds
        if symbol not in gen.return_types:
            from siec.codegen.methods import constructor_type

            if (ctor := constructor_type(gen, call, symbol)) is not None:
                return ctor

        # a '&T' return reads as the T it aliases, like a reference parameter
        return strip_reference(gen.return_types.get(symbol))

    # a cast produces its target type
    if isinstance(expr, Cast):
        expr.type = expand_alias(gen, expr.type)
        return expr.type

    # an inline assembly block produces its declared '-> T'
    if isinstance(expr, AsmBlock):
        expr.return_type = expand_alias(gen, expr.return_type)
        return expr.return_type

    # a member access yields the field's type; an aliasing field (a pointer
    # or array) keeps a const base's contract
    if isinstance(expr, Member):
        # a pure name chain may be a module's member, spelled qualified
        if (folded := fold_qualified(gen, expr, scope)) is not None:
            return expr_sie_type(gen, folded, scope)

        # an unnamed member's fields hoist into its struct: 'r.value'
        # resolves through the 'r.#n' that carries it
        hoist_member(gen, expr, scope)

        base_name = expr_sie_type(gen, expr.base, scope)

        # a raw array's 'length' is its compile-time element count
        if raw_array(strip_const(base_name)) is not None and expr.field == "length":
            return "u64"
        info = type_info(gen, base_name)
        if info is None:
            return None

        field_type = info.field(expr.field)[1]
        if is_const(base_name) and is_aliasing(field_type) and not is_const(field_type):
            return f"const {field_type}"

        return field_type

    # indexing yields the element type, one '[]' or '*' shorter; an aliasing
    # element keeps a const base's contract
    if isinstance(expr, Index):
        base = expr_sie_type(gen, expr.base, scope)
        if base is None:
            return None

        stripped = strip_const(base)
        if (raw := raw_array(stripped)) is not None and not raw[2]:
            element = raw[0]
        else:
            element = stripped[:-2] if stripped.endswith("[]") else stripped.removesuffix("*")

        if is_const(base) and is_aliasing(element):
            return f"const {element}"

        return element

    # a slice is a view with its base's array type
    if isinstance(expr, Slice):
        return expr_sie_type(gen, expr.base, scope)

    # '&' yields a pointer to its operand's type; an address rooted in
    # const storage keeps the contract, since the pointer is an alias
    # of that storage, not a copy of its value
    if isinstance(expr, UnaryOp) and expr.op == "&":
        operand = expr_sie_type(gen, expr.operand, scope)
        if operand is None:
            return None

        pointer = f"{strip_const(operand)}*"
        return f"const {pointer}" if const_chain(gen, expr.operand, scope) else pointer

    # '*' dereferences a pointer: the element type its 'p[0]' spelling reads
    if isinstance(expr, UnaryOp) and expr.op == "*":
        return expr_sie_type(gen, Index(expr.operand, IntLiteral(0)), scope)

    # 'A::member' carries its enum's type name, dotted spellings
    # resolving to the registered one; an 'S::m' whose base is no enum
    # types as a reference to the method
    if isinstance(expr, EnumMember):
        from siec.codegen.enums import resolve_enum

        try:
            name, error = resolve_enum(gen, expr.enum), None
        except (NameError, TypeError) as raised:
            name, error = None, raised

        if name is None or name not in gen.enums:
            from siec.codegen.methods import method_reference_type

            if (fn := method_reference_type(gen, expr)) is not None:
                return fn

        if error is not None:
            raise error

        return name

    # a char literal is exactly a 'char'
    if isinstance(expr, CharLiteral):
        return "char"

    # a ternary carries its arms' type; either arm may pin it down
    if isinstance(expr, Ternary):
        return (expr_sie_type(gen, expr.then, scope)
                or expr_sie_type(gen, expr.orelse, scope))

    # a struct operand's binary operator types as the method call it
    # desugars to: 'a + b' is 'a.add(b)'
    if isinstance(expr, BinaryOp):
        if (rewritten := operator_call(gen, expr, scope)) is not None:
            return expr_sie_type(gen, rewritten, scope)

    return None


def const_chain(gen: CodeGenerator, expr: Expr, scope: dict) -> bool:
    """
    Whether an lvalue chain passes through anything 'const': the expression
    itself, or any member, index, or dereference link it reads through.

    Member and index links drop 'const' from a non-aliasing element's value,
    which is right for a copy but not for the storage it came from; walking
    the links finds the contract wherever it was declared.
    """
    node = expr
    while True:
        if is_const(expr_sie_type(gen, node, scope)):
            return True

        if isinstance(node, (Member, Index)):
            node = node.base
        elif isinstance(node, UnaryOp) and node.op == "*":
            node = node.operand
        else:
            return False


def infer_type(gen: CodeGenerator, expr: Expr, scope: dict) -> str | None:
    """
    Infer the Sie type an unannotated 'let' adopts from its initializer;
    None when the expression doesn't pin one down.
    """
    # named values, calls, casts, members, and the rest carry declared types;
    # a copy of a non-aliasing const value is an independent, mutable value
    declared = expr_sie_type(gen, expr, scope)
    if declared is not None:
        if is_const(declared) and not is_aliasing(strip_const(declared)):
            return strip_const(declared)

        return declared

    # literals default like they do in any untyped context
    if isinstance(expr, IntLiteral):
        return "i32"

    if isinstance(expr, FloatLiteral):
        return "f64"

    if isinstance(expr, StrLiteral):
        return "char*"

    if isinstance(expr, BoolLiteral):
        return "bool"

    # a size is a byte count, defaulting to u64 like it does in any context
    if isinstance(expr, SizeOf):
        return "u64"

    # a bare 'null' is an opaque pointer until a context types it
    if isinstance(expr, NullLiteral):
        return "opaque*"

    # 'not' yields a bool; '-' and '~' keep their operand's type; '*' types
    # only through its operand's declared type, which expr_sie_type read
    if isinstance(expr, UnaryOp):
        if expr.op == "*":
            return None

        return "bool" if expr.op == "not" else infer_type(gen, expr.operand, scope)

    if isinstance(expr, BinaryOp):
        if expr.op in ("and", "or") or expr.op in COMPARISONS:
            return "bool"

        # arithmetic keeps its operands' type; a declared operand wins, so a
        # literal beside it adapts as in any typed context
        return (expr_sie_type(gen, expr.left, scope)
                or expr_sie_type(gen, expr.right, scope)
                or infer_type(gen, expr.left, scope)
                or infer_type(gen, expr.right, scope))

    # a ternary takes its arms' type, a declared arm winning over a literal
    if isinstance(expr, Ternary):
        return (infer_type(gen, expr.then, scope)
                or infer_type(gen, expr.orelse, scope))

    return None


def untyped_reason(gen: CodeGenerator, expr: Expr, scope: dict) -> Exception | None:
    """
    The precise error behind an initializer with no inferable type, when
    there is one: an unknown function or variable names itself, and a
    known function that returns nothing says so.
    """
    if isinstance(expr, MethodCall):
        from siec.codegen.methods import resolve_method

        symbol = resolve_method(gen, expr_sie_type(gen, expr.receiver, scope),
                                expr.method)
        if symbol is None:
            return TypeError(f"receiver has no method {expr.method!r}")

        return TypeError(f"method {expr.method!r} returns no value")

    if isinstance(expr, Call) and expr.name not in scope:
        def generic_reason(symbol):
            # a generic call fails to type when its template returns
            # nothing, or when its type arguments cannot be inferred
            template = gen.generic_functions.get(symbol)
            if template is None:
                return None

            if template.return_type is None:
                return TypeError(f"function {expr.name!r} returns no value")

            from siec.codegen.generics import resolve_generic_call
            try:
                resolve_generic_call(gen, template, expr, scope)
            except TypeError as error:
                return error

            return None

        if "." in expr.name and "::" not in expr.name:
            symbol = gen.resolve_qualified(expr.name.split("."))
            if symbol is None:
                # a dotted method call may still name a real, void method
                from siec.codegen.methods import method_call

                if (found := method_call(gen, expr, scope)) is not None:
                    return TypeError(f"function {expr.name!r} returns no value")

                return NameError(f"undefined function {expr.name!r}")

            if (reason := generic_reason(symbol)) is not None:
                return reason

            return TypeError(f"function {expr.name!r} returns no value")

        if "::" in expr.name:
            from siec.codegen.methods import qualified_method

            if qualified_method(gen, expr.name) is not None:
                return TypeError(f"function {expr.name!r} returns no value")

            return NameError(f"undefined function {expr.name!r}")

        symbol = gen.resolve_symbol(expr.name)
        if not gen.sees(expr.name) or (symbol not in gen.return_types
                                       and symbol not in gen.globals
                                       and symbol not in gen.generic_functions):
            return NameError(f"undefined function {expr.name!r}")

        if (reason := generic_reason(symbol)) is not None:
            return reason

        return TypeError(f"function {expr.name!r} returns no value")

    if isinstance(expr, Var) and expr.name not in scope:
        symbol = gen.resolve_symbol(expr.name)

        # a bare generic name has no type of its own: it adopts one from
        # a function-typed context, or from explicit arguments
        if gen.sees(expr.name) and symbol in gen.generic_functions:
            return TypeError(f"cannot infer type arguments for generic "
                             f"function {expr.name!r}: annotate a function "
                             f"type or spell '{expr.name}<...>'")

        if not gen.sees(expr.name) or (expr.name not in gen.constants
                                       and symbol not in gen.globals
                                       and symbol not in gen.param_types):
            return NameError(f"undefined variable {expr.name!r}")

    return None


def fold_qualified(gen: CodeGenerator, expr: Expr, scope: dict):
    """
    Fold a pure 'a.b.name' member chain into the Var its dotted name
    resolves to through the file's module bindings; None for any other
    shape, or when no prefix is bound. A scoped variable shadows a binding.
    """
    names, node = [], expr
    while isinstance(node, Member):
        names.append(node.field)
        node = node.base

    if not isinstance(node, Var) or node.name in scope:
        return None

    names.append(node.name)
    names.reverse()

    symbol = gen.resolve_qualified(names)
    return Var(symbol, qualified=True) if symbol is not None else None


def enum_backing(gen: CodeGenerator, name: str | None) -> str | None:
    """
    Map an enum type name to its backing numeric type name, keeping any
    'const' marking; other names pass through unchanged.
    """
    info = gen.enums.get(strip_const(name)) if name is not None else None
    if info is None:
        return name

    return f"const {info.backing}" if is_const(name) else info.backing


def signedness(gen: CodeGenerator, expr: Expr, scope: dict) -> str | None:
    """
    Infer the signedness of an expression; None when it has no fixed one.
    """
    # named values take the signedness of their declared Sie type; an
    # enum-typed value takes its backing type's
    if isinstance(expr, (Var, Call, Member, Index, EnumMember)):
        return type_signedness(enum_backing(gen, expr_sie_type(gen, expr, scope)))

    # a dereference reads a stored element: its declared type's signedness
    if isinstance(expr, UnaryOp) and expr.op == "*":
        return type_signedness(enum_backing(gen, expr_sie_type(gen, expr, scope)))

    # arithmetic keeps the signedness of its operands; literals adapt to either
    if isinstance(expr, UnaryOp) and expr.op in ("-", "~"):
        return signedness(gen, expr.operand, scope)

    if isinstance(expr, BinaryOp) and (expr.op in ARITHMETIC or expr.op == "**"):
        return signedness(gen, expr.left, scope) or signedness(gen, expr.right, scope)

    # a ternary keeps its arms' signedness; literals adapt to either
    if isinstance(expr, Ternary):
        return signedness(gen, expr.then, scope) or signedness(gen, expr.orelse, scope)

    return None


def check_signedness(gen: CodeGenerator, expr: BinaryOp, scope: dict) -> str | None:
    """
    Reject an operation mixing a signed and an unsigned operand,
    returning the signedness the operands agree on.
    """
    left = signedness(gen, expr.left, scope)
    right = signedness(gen, expr.right, scope)

    if left is not None and right is not None and left != right:
        raise TypeError(f"cannot apply {expr.op!r} to {left} and {right} operands")

    return left or right


def numeric_class(type_name: str | None) -> tuple[str, int] | None:
    """
    Classify a scalar numeric type name as its ('i'|'u'|'f', width), else None.
    """
    type_name = strip_const(type_name)
    if type_name and type_name[0] in "iuf" and type_name[1:].isdigit():
        return type_name[0], int(type_name[1:])

    return None


def value_class(gen: CodeGenerator, value: ir.Value, expr: Expr,
                scope: dict) -> tuple[str, int] | None:
    """
    Classify an emitted value's numeric prefix and width, from its type and signedness.
    """
    # prefer the declared type name when the expression has one; an
    # enum-typed value classifies as its backing type
    declared = numeric_class(enum_backing(gen, expr_sie_type(gen, expr, scope)))
    if declared is not None:
        return declared

    # otherwise read the width from the LLVM type and the prefix from signedness
    if isinstance(value.type, ir.FloatType):
        return "f", 32

    if isinstance(value.type, ir.DoubleType):
        return "f", 64

    if isinstance(value.type, ir.IntType):
        prefix = {"signed": "i", "unsigned": "u"}.get(signedness(gen, expr, scope))
        return (prefix, value.type.width) if prefix is not None else None

    return None


def type_info(gen: CodeGenerator, type_name: str | None) -> StructInfo | None:
    """
    Return the fields of a struct or array type name, or None for other types.
    """
    # a 'const' base has the same fields as its represented type
    type_name = strip_const(type_name)

    # a sized 'X[N]' carries the same fields as the 'X[]' it declares
    if (sized := sized_array(type_name)) is not None:
        type_name = sized[0]

    # an 'X[]' array exposes two synthetic fields: 'data' (X*) and 'length' (u64)
    if type_name and type_name.endswith("[]"):
        element = type_name[:-2]
        fields = [Field("data", f"{element}*"), Field("length", "u64")]
        return StructInfo(resolve_type(type_name, gen.structs), fields)

    return gen.structs.get(type_name)


def unnamed_hops(gen: CodeGenerator, info: StructInfo, name: str) -> list[str] | None:
    """
    The chain of unnamed '#n' members leading to a hoisted field, or None
    when no unnamed member carries it.
    """
    for field in info.fields or ():
        if not field.name.startswith("#"):
            continue

        inner = type_info(gen, field.type)
        if inner is None or inner.fields is None:
            continue

        if any(f.name == name for f in inner.fields):
            return [field.name]

        if (deeper := unnamed_hops(gen, inner, name)) is not None:
            return [field.name, *deeper]

    return None


def hoist_member(gen: CodeGenerator, expr: Member, scope: dict) -> None:
    """
    Splice unnamed-member hops into a member chain, in place: when the
    base's struct lacks the field but an unnamed '#n' member's type
    carries it, 'r.value' resolves as 'r.#n.value', C-style.
    """
    info = type_info(gen, expr_sie_type(gen, expr.base, scope))
    if info is None or info.fields is None:
        return

    if any(f.name == expr.field for f in info.fields):
        return

    for hop in unnamed_hops(gen, info, expr.field) or ():
        expr.base = Member(expr.base, hop)


def member_field(gen: CodeGenerator, expr: Member, scope: dict) -> tuple[int, str]:
    """
    Resolve a member access to its field index and Sie type, checking the base has fields.
    """
    hoist_member(gen, expr, scope)

    base_type = expr_sie_type(gen, expr.base, scope)
    info = type_info(gen, base_type)
    if info is None:
        raise TypeError(f"cannot access field {expr.field!r} on non-struct type {base_type}")

    return info.field(expr.field)
