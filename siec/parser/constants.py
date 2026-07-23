"""Parsing of '@const' and '@macro' declarations."""

from siec.ast import Const
from siec.parser.expressions import parse_expression
from siec.parser.stream import TokenStream
from siec.parser.types import parse_type


def parse_const(ts: TokenStream) -> Const:
    """
    Parse '@const name[: T] = <value>;', the type annotation optional.
    """
    line = ts.peek().line
    ts.expect("sym", "@")
    ts.expect("ident", "const")
    name = ts.expect("ident").value

    type_ = None
    if ts.peek().syntax == ":":
        ts.next()
        type_ = parse_type(ts)

    ts.expect("sym", "=")
    value = parse_expression(ts)
    ts.expect("sym", ";")

    return Const(name, type_, value, line=line)


def parse_macro(ts: TokenStream) -> Const:
    """
    Parse an '@macro' declaration, substituted at each use:

        @macro name = <expr>;              object-like: a bare 'name' expands
        @macro name(a, b) = <expr>;        function-like, an expression
        @macro name(a, b) { ... }          function-like, a block; 'emit'
                                           inside produces the call's value
    """
    # deferred import: statements and expressions are mutually recursive
    from siec.parser.statements import parse_block

    line = ts.peek().line
    ts.expect("sym", "@")
    ts.expect("ident", "macro")
    name = ts.expect("ident").value

    # '(' opens a function-like macro's parameter list; without one the
    # macro is object-like, expanding on its bare name
    params = None
    if ts.peek().syntax == "(":
        ts.next()
        params = []
        while ts.peek().syntax != ")":
            if params:
                ts.expect("sym", ",")

            params.append(ts.expect("ident").value)
        ts.next()

    if ts.peek().syntax == "=":
        ts.next()
        value = parse_expression(ts)
        ts.expect("sym", ";")
        return Const(name, None, value, params=params, is_macro=True, line=line)

    body = parse_block(ts)
    return Const(name, None, None, params=params, body=body, is_macro=True,
                 line=line)
