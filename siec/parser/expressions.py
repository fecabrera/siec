"""Parsing of expressions: literals, variables, and calls."""

from siec.ast import (
    AggregateLiteral,
    ArrayLiteral,
    AsmBlock,
    BinaryOp,
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
    UnaryOp,
    Var,
)
from siec.lexer.token import int_value
from siec.parser.stream import TokenStream
from siec.parser.types import parse_type

# binary operators from loosest to tightest; each level folds left-associatively
LEVELS = [
    {"or"},                             # logical or
    {"and"},                            # logical and
    {"<", ">", "<=", ">=", "==", "!="}, # comparisons
    {"|"},                              # bitwise or
    {"^"},                              # bitwise xor
    {"&"},                              # bitwise and
    {"<<", ">>"},                       # shifts
    {"+", "-"},                         # additive
    {"*", "/", "%"},                    # multiplicative
]


def parse_expression(ts: TokenStream) -> Expr:
    """
    Parse an expression: a possible ternary over the binary precedence
    ladder over primaries.
    """
    return parse_ternary(ts)


def parse_ternary(ts: TokenStream) -> Expr:
    """
    Parse 'cond ? then : orelse', the loosest operator, folding
    right-associatively: 'a ? b : c ? d : e' nests in the else arm.
    """
    condition = parse_binary(ts, 0)

    if ts.peek().syntax != "?":
        return condition

    ts.next()
    then = parse_expression(ts)
    ts.expect("sym", ":")
    return Ternary(condition, then, parse_expression(ts))


def parse_binary(ts: TokenStream, level: int) -> Expr:
    """
    Parse the binary operators at one precedence level, recursing tighter.
    """
    if level == len(LEVELS):
        return parse_cast(ts)

    left = parse_binary(ts, level + 1)

    while ts.peek().syntax in LEVELS[level]:
        op = ts.next().value
        left = BinaryOp(op, left, parse_binary(ts, level + 1))

    return left


def parse_cast(ts: TokenStream) -> Expr:
    """
    Parse 'expr as T' casts, which bind tighter than any binary operator and
    chain left to right, but looser than power and the unary prefixes.
    """
    left = parse_power(ts)

    while ts.peek().syntax == "as":
        ts.next()
        left = Cast(left, parse_type(ts))

    return left


def parse_power(ts: TokenStream) -> Expr:
    """
    Parse '**' chains, which bind tightest and fold right-associatively.
    """
    left = parse_primary(ts)

    if ts.peek().syntax == "**":
        ts.next()
        return BinaryOp("**", left, parse_power(ts))

    return left


def parse_primary(ts: TokenStream) -> Expr:
    """
    Parse a primary expression: an integer literal, string literal, variable, or call.
    """
    tok = ts.next()

    # prefix '-', '~', 'not', and '&' bind tighter than any binary operator
    if tok.syntax == "-":
        # fold '-' over a numeric literal into a negative constant, keeping it instruction-free
        if ts.peek().kind == "int":
            return IntLiteral(-int_value(ts.next().value))

        if ts.peek().kind == "float":
            return FloatLiteral(-float(ts.next().value))

        return UnaryOp("-", parse_primary(ts))

    if tok.syntax in ("~", "not", "&"):
        return UnaryOp(tok.value, parse_primary(ts))

    # 'true' and 'false' are boolean literals
    if tok.kind == "kw" and tok.value in ("true", "false"):
        return BoolLiteral(tok.value == "true")

    # '@asm' opens an inline assembly block
    if tok.syntax == "@" and ts.peek().value == "asm":
        return parse_asm_tail(ts)

    # 'null' is the pointer literal
    if tok.kind == "kw" and tok.value == "null":
        return NullLiteral()

    # '(' groups a full subexpression
    if tok.syntax == "(":
        expr = parse_expression(ts)
        ts.expect("sym", ")")
        return parse_postfix(ts, expr)

    # '{a, b, ...}' is an aggregate literal filling a struct or array's
    # fields, '{x = a, y = b, ...}' one filling them by name; '{ ...; emit
    # v; }' is a block expression producing a value. The shapes tell them
    # apart: a literal holds comma-separated fields, a block holds statements.
    if tok.syntax == "{":
        if is_aggregate(ts):
            elements = []
            while ts.peek().syntax != "}":
                elements.append(parse_expression(ts))

                # a comma follows every element but, optionally, the last
                if ts.peek().syntax != "}":
                    ts.expect("sym", ",")
            
            ts.expect("sym", "}")

            return parse_postfix(ts, AggregateLiteral(elements))

        if is_named_aggregate(ts):
            elements, names = [], []
            while ts.peek().syntax != "}":
                names.append(ts.expect("ident").value)
                ts.expect("sym", "=")
                elements.append(parse_expression(ts))

                if ts.peek().syntax != "}":
                    ts.expect("sym", ",")
            
            ts.expect("sym", "}")

            return parse_postfix(ts, AggregateLiteral(elements, names))

        # deferred import: statements and expressions are mutually recursive
        from siec.parser.statements import parse_statement

        body = []
        while ts.peek().syntax != "}":
            body.append(parse_statement(ts))
        ts.expect("sym", "}")

        return parse_postfix(ts, BlockExpr(body))

    # '[a, b, ...]' is an array literal, building a fat array from its elements
    if tok.syntax == "[":
        elements = []
        while ts.peek().syntax != "]":
            elements.append(parse_expression(ts))

            # a comma follows every element but, optionally, the last
            if ts.peek().syntax != "]":
                ts.expect("sym", ",")
        
        ts.expect("sym", "]")

        return parse_postfix(ts, ArrayLiteral(elements))

    if tok.kind == "int":
        return IntLiteral(int_value(tok.value))

    if tok.kind == "float":
        return FloatLiteral(float(tok.value))

    if tok.kind == "str":
        return parse_postfix(ts, StrLiteral(tok.value))

    if tok.kind == "char":
        return CharLiteral(tok.value)

    # an identifier is an enum member if followed by '::', a call if
    # followed by '(', and a variable otherwise
    if tok.kind == "ident":
        # 'sizeof(T)' takes a type or a variable's name, measured at codegen
        if tok.value == "sizeof" and ts.peek().syntax == "(":
            ts.next()
            name = parse_type(ts)
            ts.expect("sym", ")")
            return parse_postfix(ts, SizeOf(name))

        if ts.peek().syntax == "::":
            ts.next()
            member = ts.expect("ident").value

            # a call after '::' is a method's fully qualified form,
            # 'S::method(s)', optionally with explicit type arguments
            method_args = None
            if ts.peek().syntax == "<":
                method_args = parse_type_arguments(ts)

            if method_args is not None or ts.peek().syntax == "(":
                ts.next()

                args = []
                while ts.peek().syntax != ")":
                    if args:
                        ts.expect("sym", ",")

                    args.append(parse_expression(ts))
                ts.expect("sym", ")")

                return parse_postfix(
                    ts, Call(f"{tok.value}::{member}", args, method_args))

            return parse_postfix(ts, EnumMember(tok.value, member))

        # '<A, B>(' spells a generic call's type arguments; '<A, B>::' a
        # generic type's qualified method; landing on an expression
        # terminator instead makes it a bare reference to the instance;
        # any other '<' stays a comparison
        type_args = None
        if ts.peek().syntax == "<":
            type_args = parse_type_arguments(
                ts, followers=("(", ";", ",", ")", "]", "}", "::"))

        # 'S<T>::method(...)' calls through the generic instance, the
        # type arguments joining the receiver type's name
        if type_args is not None and ts.peek().syntax == "::":
            ts.next()
            member = ts.expect("ident").value
            name = f"{tok.value}<{','.join(type_args)}>::{member}"

            method_args = None
            if ts.peek().syntax == "<":
                method_args = parse_type_arguments(ts)

            # without a call, the name is a bare reference to the method
            if method_args is None and ts.peek().syntax != "(":
                return parse_postfix(
                    ts, EnumMember(f"{tok.value}<{','.join(type_args)}>", member))
            ts.next()

            args = []
            while ts.peek().syntax != ")":
                if args:
                    ts.expect("sym", ",")

                args.append(parse_expression(ts))
            ts.expect("sym", ")")

            return parse_postfix(ts, Call(name, args, method_args))

        if type_args is not None and ts.peek().syntax != "(":
            return parse_postfix(ts, Var(tok.value, type_args=type_args))

        if type_args is not None or ts.peek().syntax == "(":
            ts.next()

            # comma-separated argument expressions up to the closing ')'
            args = []
            while ts.peek().syntax != ")":
                if args:
                    ts.expect("sym", ",")

                args.append(parse_expression(ts))
            ts.expect("sym", ")")

            expr = Call(tok.value, args, type_args)
        else:
            expr = Var(tok.value)

        return parse_postfix(ts, expr)

    raise SyntaxError(f"line {tok.line}: unexpected token {tok.value!r} in expression")


def parse_type_arguments(ts: TokenStream,
                         followers: tuple = ("(",)) -> list[str] | None:
    """
    Speculatively parse the '<A, B>' of a generic call or reference,
    which must land directly on one of the follower tokens — a call's
    '(', or an expression terminator for a bare 'f<i32>' reference.
    Anything else — a '<' that reads as a comparison — rewinds the
    stream untouched and returns None.
    """
    from siec.parser.types import close_angle, parse_type

    saved = ts.pos
    outer = getattr(ts, "angle_journal", None)
    ts.angle_journal = journal = []

    try:
        ts.next()  # the '<'
        args = [parse_type(ts)]
        while ts.peek().syntax == ",":
            ts.next()
            args.append(parse_type(ts))
        close_angle(ts)
        ok = ts.peek().syntax in followers
    except SyntaxError:
        ok = False
    finally:
        ts.angle_journal = outer

    if not ok:
        ts.pos = saved
        for tok, value in reversed(journal):
            tok.value = value
        return None

    # an enclosing speculation must be able to undo this one's splits too
    if outer is not None:
        outer.extend(journal)

    return args


def parse_clobbers(ts: TokenStream) -> list[str]:
    """
    Parse '@clobbers' arguments: one or more strings naming the registers
    and other state an assembly body clobbers.
    """
    ts.expect("sym", "(")

    clobbers = [ts.expect("str").value]
    while ts.peek().syntax == ",":
        ts.next()
        clobbers.append(ts.expect("str").value)

    ts.expect("sym", ")")
    return clobbers


def parse_asm_tail(ts: TokenStream) -> AsmBlock:
    """
    Parse an inline '@asm' block after its '@': an optional '@clobbers',
    an optional '(name, ...)' operand list, an optional '-> T', and the
    raw assembly body.
    """
    line = ts.expect("ident", "asm").line

    clobbers = []
    if ts.peek().syntax == "@":
        ts.next()
        ts.expect("ident", "clobbers")
        clobbers = parse_clobbers(ts)

    # operands are names from the enclosing scope, interpolated by name
    args = []
    if ts.peek().syntax == "(":
        ts.next()
        while ts.peek().syntax != ")":
            if args:
                ts.expect("sym", ",")

            args.append(ts.expect("ident").value)
        ts.next()

    return_type = None
    if ts.peek().value == "->":
        ts.next()
        return_type = parse_type(ts)

    if ts.peek().kind != "asm":
        raise SyntaxError(f"line {ts.peek().line}: an '@asm' block needs "
                          "an assembly body")

    return AsmBlock(ts.next().value, args, return_type, clobbers, line=line)


def is_aggregate(ts: TokenStream) -> bool:
    """
    Decide whether an open '{' holds an aggregate literal, peeking past its
    first expression for the ',' or '}' a literal would show; the cursor is
    restored either way.
    """
    # '{}' is the empty aggregate
    if ts.peek().syntax == "}":
        return True

    start = ts.pos
    try:
        parse_expression(ts)
        return ts.peek().syntax in (",", "}")
    except SyntaxError:
        # not even an expression: only statements can follow
        return False
    finally:
        ts.pos = start


def is_named_aggregate(ts: TokenStream) -> bool:
    """
    Decide whether an open '{' holds a named aggregate: an ident and '='
    open one, and its first value ends at the ',' or '}' a literal would
    show, where a block's assignment statement needs ';'. The cursor is
    restored either way.
    """
    if ts.peek().kind != "ident" or ts.peek(1).syntax != "=":
        return False

    start = ts.pos
    try:
        ts.next()
        ts.next()
        parse_expression(ts)
        return ts.peek().syntax in (",", "}")
    except SyntaxError:
        return False
    finally:
        ts.pos = start


def ident_chain(expr: Expr) -> list[str] | None:
    """
    The names of a pure 'a.b.c' member chain over a variable root; None
    for any other shape.
    """
    names = []
    while isinstance(expr, Member):
        names.append(expr.field)
        expr = expr.base

    if not isinstance(expr, Var):
        return None

    names.append(expr.name)
    names.reverse()
    return names


def parse_postfix(ts: TokenStream, expr: Expr) -> Expr:
    """
    Apply postfix '[index]', '[from:to]', and '.field' chains to an expression:
    variables, call results, groupings, and literals alike. A '(' after a
    pure name chain calls it by its dotted name ('libc.stdio.printf(...)').
    """
    while True:
        # '::' after a pure name chain reaches an enum's member through
        # its module: 'shapes.Color::RED'
        if (ts.peek().syntax == "::" and (names := ident_chain(expr)) is not None
                and len(names) > 1):
            ts.next()
            expr = EnumMember(".".join(names), ts.expect("ident").value)
            continue

        if (ts.peek().syntax in ("(", "<") and (names := ident_chain(expr)) is not None
                and len(names) > 1):
            type_args = None
            if ts.peek().syntax == "<":
                type_args = parse_type_arguments(
                    ts, followers=("(", ";", ",", ")", "]", "}"))

            # '<...>' landing on a terminator is a reference to the
            # dotted name's generic instance, not a call
            if type_args is not None and ts.peek().syntax != "(":
                expr = Var(".".join(names), type_args=type_args)
                continue

            if type_args is not None or ts.peek().syntax == "(":
                ts.next()

                args = []
                while ts.peek().syntax != ")":
                    if args:
                        ts.expect("sym", ",")

                    args.append(parse_expression(ts))
                ts.expect("sym", ")")

                expr = Call(".".join(names), args, type_args)
                continue

        # a call on a field of anything but a pure name chain is a method
        # on that receiver expression: 'get(i).init(...)'
        if (ts.peek().syntax in ("(", "<") and isinstance(expr, Member)
                and ident_chain(expr) is None):
            type_args = None
            if ts.peek().syntax == "<":
                type_args = parse_type_arguments(ts)

            if type_args is not None or ts.peek().syntax == "(":
                ts.next()

                args = []
                while ts.peek().syntax != ")":
                    if args:
                        ts.expect("sym", ",")

                    args.append(parse_expression(ts))
                ts.expect("sym", ")")

                expr = MethodCall(expr.base, expr.field, args, type_args)
                continue

        if ts.peek().syntax not in ("[", "."):
            return expr

        if ts.next().value == "[":
            # a ':' anywhere in the brackets makes it a slice, either bound optional
            start = None if ts.peek().syntax == ":" else parse_expression(ts)

            if ts.peek().syntax == ":":
                ts.next()
                stop = None if ts.peek().syntax == "]" else parse_expression(ts)
                ts.expect("sym", "]")
                expr = Slice(expr, start, stop)
            else:
                ts.expect("sym", "]")
                expr = Index(expr, start)
        else:
            expr = Member(expr, ts.expect("ident").value)

    return expr
