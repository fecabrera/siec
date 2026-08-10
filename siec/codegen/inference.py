"""Type analysis of expressions: Sie types, signedness, and numeric classes.

Everything here answers questions about expressions - what type is this,
how does it classify - without emitting any IR.
"""

from llvmlite import ir

from siec.ast import (
    ArrayLiteral,
    AsmBlock,
    BinaryOp,
    Block,
    BlockExpr,
    BoolLiteral,
    CachedExpr,
    Call,
    Cast,
    CharLiteral,
    ClosureExpr,
    EnumMember,
    Expr,
    Field,
    FloatLiteral,
    Index,
    IntLiteral,
    Member,
    MethodCall,
    Move,
    NullLiteral,
    SizeOf,
    Slice,
    StrLiteral,
    Ternary,
    TupleLiteral,
    Try,
    TypeId,
    TypeName,
    TypeOf,
    UnaryOp,
    Var,
)
from siec.codegen.aliases import expand_alias
from siec.codegen.generator import CodeGenerator, StructInfo
from siec.codegen.resolution import fold_qualified
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
OPERATOR_METHODS = {"+": "add", "-": "sub", "*": "mul", "/": "div", "%": "rem",
                    "==": "eq", "!=": "eq",
                    "<": "cmp", ">": "cmp", "<=": "cmp", ">=": "cmp"}


def integer_literal_type(value: int) -> str:
    """The signed builtin type an untyped integer literal defaults to."""
    if -2**31 <= value < 2**31:
        return "i32"
    if -2**63 <= value < 2**63:
        return "i64"
    return "i128"


def result_arms(name: str | None) -> tuple[str | None, str] | None:
    """
    The value and error types a 'Result' spelling carries, or None for
    any other type. 'Result<E>' has no value, so its value type is None.
    """
    from siec.codegen.generics import split_generic

    split = split_generic(strip_const(strip_reference(strip_const(name) or "")))
    if split is None or split[0] != "Result":
        return None

    args = split[1]
    if len(args) == 1:
        return None, args[0]

    return (args[0], args[1]) if len(args) == 2 else None


def try_arms(gen: CodeGenerator, expr: Try, scope: dict) -> tuple[str | None, str]:
    """
    The value and error types a 'try' unwraps, or the reason its operand
    gives it nothing to unwrap.
    """
    carried = expr_sie_type(gen, expr.result, scope)
    arms = result_arms(carried)
    if arms is None:
        if carried is None:
            raise TypeError("'try' takes a Result to unwrap; this "
                            "expression has no value")

        raise TypeError(f"'try' takes a Result to unwrap; this one "
                        f"is {carried!r}")

    return arms


def valueless_try(name: str | None) -> TypeError:
    """
    The error a 'try' over a valueless result gives where a value is
    wanted: 'Result<E>' carries only the error, so there is nothing to
    take and nothing for its arm to emit.
    """
    return TypeError(f"a {strip_const(name)!r} carries no value, so this "
                     "'try' has none to give: it stands on its own")


def operator_call(gen: "CodeGenerator", expr: BinaryOp, scope: dict) -> Expr | None:
    """
    Rewrite a binary operator over a struct operand into the method call
    it means: 'a + b' is 'a.add(b)', each overload picked by b's type,
    'a != b' is equality negated, 'not a.eq(b)', and an ordering
    compares 'cmp's sign, 'a < b' as 'a.cmp(b) < 0'. None for any other
    operand, whose operators keep their instructions.
    """
    method = OPERATOR_METHODS.get(expr.op)
    if method is None:
        return None

    # enum-typed operands keep their integer arithmetic; arrays take the
    # shorthand too, through their 'T[]::m' methods
    name = strip_const(expr_sie_type(gen, expr.left, scope) or "")
    if name in gen.enums or (name not in gen.structs
                             and not name.endswith("[]")):
        return None

    from siec.codegen.ownership import inherit_expression_identity

    call = MethodCall(expr.left, method, [expr.right])
    if expr.op == "!=":
        return inherit_expression_identity(expr, UnaryOp("not", call))

    if method == "cmp":
        return inherit_expression_identity(
            expr, BinaryOp(expr.op, call, IntLiteral(0)))

    return inherit_expression_identity(expr, call)


def item_call(gen: "CodeGenerator", expr: Index, scope: dict,
              method: str, value: Expr | None = None) -> MethodCall | None:
    """
    Rewrite indexed access on a struct into its operator method:
    'a[key]' is 'a.get_item(key)', and 'a[key] = value' asks for
    'a.set_item(key, value)'.

    Arrays, pointers, raw arrays, and tuples have native storage indexing
    and never take this path.
    """
    name = strip_const(strip_reference(
        expr_sie_type(gen, expr.base, scope) or ""))
    if (not name or name.startswith("Tuple<")
            or raw_array(name) is not None
            or name.endswith("[]") or name.endswith("*")
            or name not in gen.structs):
        return None

    from siec.codegen.methods import resolve_method

    if resolve_method(gen, name, method) is None:
        return None

    from siec.codegen.ownership import inherit_expression_identity

    args = [expr.index] if value is None else [expr.index, value]
    return inherit_expression_identity(
        expr, MethodCall(expr.base, method, args))


def is_float(type_: ir.Type) -> bool:
    """
    Whether an LLVM type is a floating-point scalar.
    """
    return isinstance(type_, (ir.FloatType, ir.DoubleType))


def expr_sie_type(gen: CodeGenerator, expr: Expr, scope: dict) -> str | None:
    """
    Infer the Sie type name of an expression; None when it has no fixed one.
    """
    if isinstance(expr, CachedExpr):
        return expr_sie_type(gen, expr.expr, scope)

    if isinstance(expr, Move):
        return expr_sie_type(gen, expr.operand, scope)

    if isinstance(expr, ClosureExpr):
        from siec.codegen.closures import closure_type

        return closure_type(expr)

    # a string or array literal is the fat array it builds; only an
    # explicit pointer context takes it as a bare pointer instead
    if isinstance(expr, StrLiteral):
        return "char[]"

    if isinstance(expr, ArrayLiteral):
        if not expr.elements:
            return None

        element = infer_type(gen, expr.elements[0], scope)
        return f"{element}[]" if element is not None else None

    # variables and calls carry their declared Sie type; a bare function
    # name carries the canonical fn type of its signature; a '&T'
    # reference parameter reads as the T it aliases
    if isinstance(expr, Var):
        if expr.name in scope:
            return strip_reference(scope[expr.name].type)

        # An object-like generic macro is written as 'name<T>' without a
        # value-argument list. Its type arguments belong to the expansion,
        # not to a generic function reference.
        from siec.codegen.macros import resolve_macro_use

        if (use := resolve_macro_use(gen, expr, scope)) is not None:
            return expr_sie_type(gen, use.call, scope)

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
        from siec.codegen.constants import constant_view, find_constant

        const = find_constant(gen, expr.name, getattr(expr, "module_file", None))
        if const is not None:
            if const.type is not None:
                return const.type

            with constant_view(gen, const):
                return expr_sie_type(gen, const.value, scope)

        # a global carries its declared type
        symbol = gen.resolve_symbol(expr.name)
        if symbol in gen.globals:
            return gen.globals[symbol]

        from siec.codegen.overloads import overload_candidates

        candidate = overload_candidates(gen, symbol)[0]
        if candidate in gen.param_types:
            params = ",".join(gen.param_types[candidate])
            ret = gen.return_types.get(candidate)
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
        from siec.codegen.macros import resolve_macro_use

        use = resolve_macro_use(gen, expr, scope)

        # a macro call types as its expansion: the substituted expression,
        # or a block's 'emit' value, resolved in the macro's file's view
        if use is not None:
            from siec.codegen.macros import first_emit, macro_expansion, macro_view

            expansion = macro_expansion(gen, expr)
            if isinstance(expansion, Block):
                return None

            with macro_view(gen, expr.name):
                if isinstance(expansion, BlockExpr):
                    return expr_sie_type(gen, first_emit(expansion.body).value,
                                         scope)

                return expr_sie_type(gen, expansion, scope)

        # a call through a function reference yields the reference's return
        # type, a '&T' return reading as the T it aliases
        if (expr.name in scope
                and strip_const(scope[expr.name].type).startswith(
                    ("fn(", "closure fn("))):
            return strip_reference(fn_type_parts(
                strip_const(scope[expr.name].type))[1])

        # the builtin 'enumerate(x)' types as its '__enumerate' instance
        if expr.name == "enumerate":
            from siec.codegen.methods import rewrite_enumerate

            if (rewritten := rewrite_enumerate(gen, expr, scope)) is not None:
                return expr_sie_type(gen, rewritten, scope)

        call = expr
        module = None
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

            symbol, module = gen.resolve_callee(expr.name)
            if symbol in gen.globals and strip_const(gen.globals[symbol]).startswith("fn("):
                return strip_reference(fn_type_parts(strip_const(gen.globals[symbol]))[1])

            # a dotted callee may be a method on its receiver chain, its
            # receiver joining the arguments for inference
            if symbol is None or (symbol not in gen.return_types
                                  and symbol not in gen.overloads):
                from siec.codegen.methods import method_call

                if "." in expr.name and (found := method_call(gen, expr, scope)):
                    symbol, receiver = found
                    module = None
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
                symbol = pick_overload(
                    gen, symbol, call.args, scope, module=module)
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

            # the substituted spelling mixes the template's names with
            # the call's arguments, so no view gates it
            mapping = dict(zip(template.type_params, type_args))
            gen.ungated_types += 1
            try:
                return strip_reference(
                    expand_alias(gen, substitute(template.return_type, mapping)))
            finally:
                gen.ungated_types -= 1

        # 'S(...)' constructs and types as the S it builds
        if symbol not in gen.return_types:
            from siec.codegen.methods import constructor_type

            if (ctor := constructor_type(gen, call, symbol)) is not None:
                return ctor

        # a '&T' return reads as the T it aliases, like a reference parameter
        return strip_reference(gen.return_types.get(symbol))

    # a cast produces its target type
    if isinstance(expr, Cast):
        # the written spelling expands (and gates) once; the canonical
        # result must not re-gate as if written here
        if not getattr(expr, "expanded", False):
            expr.type = expand_alias(gen, expr.type)
            expr.expanded = True

        return expr.type

    # '@typeof' is a type id, a u64 like '@typeid'
    if isinstance(expr, TypeOf):
        return "u64"

    # an inline assembly block produces its declared '-> T'
    if isinstance(expr, AsmBlock):
        expr.return_type = expand_alias(gen, expr.return_type)
        return expr.return_type

    # a 'try' takes the value its call's result carries; one carrying
    # only an error has none to take
    if isinstance(expr, Try):
        arms = result_arms(expr_sie_type(gen, expr.result, scope))
        return arms[0] if arms is not None else None

    # a member access yields the field's type; an aliasing field (a pointer
    # or array) keeps a const base's contract
    if isinstance(expr, Member):
        base_type = strip_const(expr_sie_type(gen, expr.base, scope) or "")
        if base_type.startswith("closure fn(") and expr.field == "env":
            return "opaque*"

        # a pure name chain may be a module's member, spelled qualified
        if (folded := fold_qualified(gen, expr, scope)) is not None:
            return expr_sie_type(gen, folded, scope)

        # an unnamed member's fields hoist into its struct: 'r.value'
        # resolves through the 'r.#n' that carries it
        hoist_member(gen, expr, scope)

        base_name = expr_sie_type(gen, expr.base, scope)

        # a raw array's 'length' is its compile-time element count, and
        # a tuple's is its arity
        if raw_array(strip_const(base_name)) is not None and expr.field == "length":
            return "u64"

        if strip_const(base_name or "").startswith("Tuple<") and expr.field == "length":
            return "u64"
        info = type_info(gen, base_name)
        if info is None:
            return None

        _, field_type = member_field(gen, expr, scope)
        if (sized := sized_array(strip_const(field_type))) is not None:
            field_type = sized[0]
        if is_const(base_name) and is_aliasing(field_type) and not is_const(field_type):
            return f"const {field_type}"

        return field_type

    # indexing yields the element type, one '[]' or '*' shorter; an aliasing
    # element keeps a const base's contract; a tuple's element follows its
    # constant index
    if isinstance(expr, Index):
        if (rewritten := item_call(gen, expr, scope, "get_item")) is not None:
            return expr_sie_type(gen, rewritten, scope)

        base = expr_sie_type(gen, expr.base, scope)
        if base is None:
            return None

        stripped = strip_const(base)
        if stripped.startswith("Tuple<"):
            from siec.codegen.enums import evaluate
            from siec.codegen.generics import split_generic

            args = split_generic(stripped)[1]
            try:
                index = evaluate(gen, expr.index)
            except (TypeError, NameError):
                return None

            if not 0 <= index < len(args):
                return None

            element = args[index]
            if is_const(base) and is_aliasing(element):
                return f"const {element}"

            return element

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

    # 'not' yields a bool; '-' and '~' keep their operand's type
    if isinstance(expr, UnaryOp):
        if expr.op == "not":
            return "bool"
        if expr.op in ("-", "~"):
            return expr_sie_type(gen, expr.operand, scope)

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

    # '@typename' is a baked-in string
    if isinstance(expr, TypeName):
        return "const char[]"

    # a tuple literal carries its elements' types, literals defaulting
    # like they do in any untyped context
    if isinstance(expr, TupleLiteral):
        elements = [infer_type(gen, element, scope) for element in expr.elements]
        if not all(elements):
            return None

        return expand_alias(gen, f"Tuple<{','.join(elements)}>", checked=False)

    # a ternary carries its arms' type; either arm may pin it down
    if isinstance(expr, Ternary):
        return (expr_sie_type(gen, expr.then, scope)
                or expr_sie_type(gen, expr.orelse, scope))

    # a struct operand's binary operator types as the method call it
    # desugars to: 'a + b' is 'a.add(b)'
    if isinstance(expr, BinaryOp):
        if (rewritten := operator_call(gen, expr, scope)) is not None:
            return expr_sie_type(gen, rewritten, scope)

        if expr.op in ("and", "or") or expr.op in COMPARISONS:
            return "bool"

        # 'p ± n' keeps the pointer's type, even when the integer is on the left
        if (pointer := pointer_arithmetic_type(gen, expr, scope)) is not None:
            return pointer

        # arithmetic, bitwise, and power keep an operand's declared type
        if expr.op in ARITHMETIC or expr.op == "**":
            return (expr_sie_type(gen, expr.left, scope)
                    or expr_sie_type(gen, expr.right, scope))

    return None


def pointer_arithmetic_type(gen: CodeGenerator, expr: BinaryOp,
                            scope: dict) -> str | None:
    """
    The pointer type of a ``p + n``, ``n + p``, or ``p - n`` expression,
    or None when the operator is not a pointer offset.
    """
    if expr.op not in ("+", "-"):
        return None

    left = expr_sie_type(gen, expr.left, scope)
    right = expr_sie_type(gen, expr.right, scope)
    left_ptr = strip_const(left or "").endswith("*")
    right_ptr = strip_const(right or "").endswith("*")

    if left_ptr and not right_ptr:
        return left

    if expr.op == "+" and right_ptr and not left_ptr:
        return right

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
    if isinstance(expr, CachedExpr):
        return infer_type(gen, expr.expr, scope)

    # named values, calls, casts, members, and the rest carry declared types;
    # a copy of a non-aliasing const value is an independent, mutable value
    declared = expr_sie_type(gen, expr, scope)
    if declared is not None:
        if is_const(declared) and not is_aliasing(strip_const(declared)):
            return strip_const(declared)

        return declared

    # a macro use infers as its expansion, literal defaults included;
    # a bare object-like macro reads as its call
    from siec.codegen.macros import resolve_macro_use

    use = resolve_macro_use(gen, expr, scope)
    if use is not None:
        expr = use.call

    if use is not None:
        from siec.codegen.macros import first_emit, macro_expansion, macro_view

        expansion = macro_expansion(gen, expr)
        if isinstance(expansion, Block):
            return None

        with macro_view(gen, expr.name):
            if isinstance(expansion, BlockExpr):
                return infer_type(gen, first_emit(expansion.body).value, scope)

            return infer_type(gen, expansion, scope)

    # literals default like they do in any untyped context
    if isinstance(expr, IntLiteral):
        return integer_literal_type(expr.value)

    if isinstance(expr, FloatLiteral):
        return "f64"

    if isinstance(expr, BoolLiteral):
        return "bool"

    # a size is a byte count, defaulting to u64 like it does in any
    # context, and a '@typeid' hash types the same way
    if isinstance(expr, (SizeOf, TypeId)):
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

        if (pointer := pointer_arithmetic_type(gen, expr, scope)) is not None:
            return pointer

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

    # a 'try' types as the value its result carries: it has none when the
    # call returns no Result, or one carrying only an error
    if isinstance(expr, Try):
        try:
            if try_arms(gen, expr, scope)[0] is None:
                return valueless_try(expr_sie_type(gen, expr.result, scope))
        except TypeError as error:
            return error

        return None

    from siec.codegen.macros import resolve_macro_use

    use = resolve_macro_use(gen, expr, scope)
    if use is not None:
        expr = use.call
        if gen.macros[expr.name].body is not None:
            return TypeError(f"macro {expr.name!r} does not 'emit' a value")

        from siec.codegen.macros import macro_expansion

        return untyped_reason(gen, macro_expansion(gen, expr), scope)

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
                                       and symbol not in gen.overloads
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
                                       and symbol not in gen.overloads
                                       and symbol not in gen.param_types):
            return NameError(f"undefined variable {expr.name!r}")

    return None


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
    if isinstance(expr, CachedExpr):
        return signedness(gen, expr.expr, scope)

    # named values take the signedness of their declared Sie type; an
    # enum-typed value takes its backing type's
    if isinstance(expr, (Var, Call, Member, Index, EnumMember, MethodCall)):
        return type_signedness(enum_backing(gen, expr_sie_type(gen, expr, scope)))

    # a cast names its target type's signedness directly
    if isinstance(expr, Cast):
        return type_signedness(enum_backing(gen, expr.type))

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
        return StructInfo(None, fields)

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


def check_field_access(gen: CodeGenerator, struct_type: str, field) -> None:
    """Reject direct access to a private field outside its struct's methods."""
    if field.is_private and not gen.can_access_private_field(struct_type):
        raise TypeError(f"field {field.name!r} is private")


def member_field(gen: CodeGenerator, expr: Member, scope: dict) -> tuple[int, str]:
    """
    Resolve a member access to its field index and Sie type, checking the base has fields.
    """
    hoist_member(gen, expr, scope)

    base_type = expr_sie_type(gen, expr.base, scope)
    info = type_info(gen, base_type)
    if info is None:
        # a base that types as nothing is the real story: an unbound name
        # ('unitstd.S_IRWXG') reads as a member access on thin air
        if base_type is None and (reason := untyped_reason(gen, expr.base, scope)):
            raise reason

        raise TypeError(f"cannot access field {expr.field!r} on non-struct "
                        f"type {base_type or '?'}")

    index, field_type = info.field(expr.field)

    return index, field_type


def sized_member_array(gen: CodeGenerator, expr: Expr,
                       scope: dict) -> tuple[str, str] | None:
    """Return a member's inline sized-array shape, when it has one."""
    if not isinstance(expr, Member):
        return None

    # a pure name chain may be a module's member, not a struct field
    if fold_qualified(gen, expr, scope) is not None:
        return None

    return sized_array(strip_const(member_field(gen, expr, scope)[1]))
