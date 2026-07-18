"""Parsing of expressions: literals, variables, and calls."""

from ..ast import (AggregateLiteral, ArrayLiteral, BinaryOp, BoolLiteral, Call, Cast, Expr,
                   Index, IntLiteral, Member, Slice, StrLiteral, UnaryOp, Var)
from .stream import TokenStream
from .types import parse_type

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
    Parse an expression: the binary precedence ladder over primaries.
    """
    return parse_binary(ts, 0)


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

    # prefix '-', '~', and 'not' bind tighter than any binary operator
    if tok.syntax == "-":
        # fold '-' over an int literal into a negative constant, keeping it instruction-free
        if ts.peek().kind == "int":
            return IntLiteral(-int(ts.next().value))

        return UnaryOp("-", parse_primary(ts))

    if tok.syntax in ("~", "not"):
        return UnaryOp(tok.value, parse_primary(ts))

    # 'true' and 'false' are boolean literals
    if tok.kind == "kw" and tok.value in ("true", "false"):
        return BoolLiteral(tok.value == "true")

    # '(' groups a full subexpression
    if tok.syntax == "(":
        expr = parse_expression(ts)
        ts.expect("sym", ")")
        return parse_postfix(ts, expr)

    # '{a, b, ...}' is an aggregate literal filling a struct or array's fields
    if tok.syntax == "{":
        elements = []
        while ts.peek().syntax != "}":
            if elements:
                ts.expect("sym", ",")

            elements.append(parse_expression(ts))
        ts.expect("sym", "}")

        return parse_postfix(ts, AggregateLiteral(elements))

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
        return IntLiteral(int(tok.value))

    if tok.kind == "str":
        return parse_postfix(ts, StrLiteral(tok.value))

    # an identifier is a call if followed by '(', otherwise a variable
    if tok.kind == "ident":
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
