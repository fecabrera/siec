"""Parsing of expressions: literals, variables, and calls."""

from siec.ast import (
    AggregateLiteral,
    ArrayLiteral,
    BinaryOp,
    BlockExpr,
    BoolLiteral,
    Call,
    Cast,
    EnumMember,
    Expr,
    FloatLiteral,
    Index,
    IntLiteral,
    Member,
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
                if elements:
                    ts.expect("sym", ",")

                elements.append(parse_expression(ts))
            ts.expect("sym", "}")

            return parse_postfix(ts, AggregateLiteral(elements))

        if is_named_aggregate(ts):
            elements, names = [], []
            while ts.peek().syntax != "}":
                if elements:
                    ts.expect("sym", ",")

                names.append(ts.expect("ident").value)
                ts.expect("sym", "=")
                elements.append(parse_expression(ts))
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
            if elements:
                ts.expect("sym", ",")

            elements.append(parse_expression(ts))
        ts.expect("sym", "]")

        return parse_postfix(ts, ArrayLiteral(elements))

    if tok.kind == "int":
        return IntLiteral(int_value(tok.value))

    if tok.kind == "float":
        return FloatLiteral(float(tok.value))

    if tok.kind == "str":
        return parse_postfix(ts, StrLiteral(tok.value))

    # an identifier is an enum member if followed by '::', a call if
    # followed by '(', and a variable otherwise
    if tok.kind == "ident":
        if ts.peek().syntax == "::":
            ts.next()
            member = ts.expect("ident").value
            return parse_postfix(ts, EnumMember(tok.value, member))

        if ts.peek().syntax == "(":
            ts.next()

            # comma-separated argument expressions up to the closing ')'
            args = []
            while ts.peek().syntax != ")":
                if args:
                    ts.expect("sym", ",")

                args.append(parse_expression(ts))
            ts.expect("sym", ")")

            expr = Call(tok.value, args)
        else:
            expr = Var(tok.value)

        return parse_postfix(ts, expr)

    raise SyntaxError(f"line {tok.line}: unexpected token {tok.value!r} in expression")


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


def parse_postfix(ts: TokenStream, expr: Expr) -> Expr:
    """
    Apply postfix '[index]', '[from:to]', and '.field' chains to an expression:
    variables, call results, groupings, and literals alike.
    """
    while ts.peek().syntax in ("[", "."):
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
