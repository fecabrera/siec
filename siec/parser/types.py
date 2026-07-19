"""Parsing of type annotations."""

from siec.parser.stream import TokenStream


def parse_type(ts: TokenStream) -> str:
    """
    Parse a type annotation, including pointer and array suffixes
    (e.g. 'u8**', 'char*[]', 'char[][]').
    """
    # a leading 'const' marks the mutation contract, kept as a prefix on
    # the canonical name; the represented type is unchanged
    if ts.peek().kind == "ident" and ts.peek().value == "const":
        ts.next()
        return f"const {parse_type(ts)}"

    # a leading '&' marks a reference: a parameter passed by a hidden pointer
    if ts.peek().syntax == "&":
        ts.next()
        return f"&{parse_type(ts)}"

    # '@raw<T>[N]' is an inline fixed-size array; 'fn(A, B) -> T' a function
    # reference type; anything else a base type name; each may be followed
    # by any mix of '*'s (which the lexer may have glued into '**' tokens)
    # and '[]' or '[N]' suffixes
    if ts.peek().syntax == "@":
        ts.next()
        ts.expect("ident", "raw")
        ts.expect("sym", "<")
        element = parse_type(ts)
        ts.expect("sym", ">")
        ts.expect("sym", "[")
        name = f"raw<{element}>[{parse_size(ts)}]"
        ts.expect("sym", "]")
    elif ts.peek().value == "fn":
        name = parse_fn_type(ts)
    else:
        name = ts.expect("ident").value

    while True:
        if ts.peek().value in ("*", "**"):
            name += ts.next().value
        elif ts.peek().value == "[":
            ts.next()

            if ts.peek().value != "]":
                name += f"[{parse_size(ts)}]"
                ts.expect("sym", "]")
            else:
                ts.expect("sym", "]")
                name += "[]"
        else:
            return name


def parse_size(ts: TokenStream) -> str:
    """
    Parse an array size as its canonical text: a constant integer
    expression — literals, '@const' names, 'sizeof', or any mix. A plain
    literal normalizes to decimal, so '[0x10]' and '[16]' agree; anything
    else keeps its tokens for codegen to evaluate.
    """
    from siec.ast import IntLiteral
    from siec.parser.expressions import parse_expression

    start = ts.pos
    size = parse_expression(ts)
    if isinstance(size, IntLiteral):
        return str(size.value)

    return " ".join(tok.value for tok in ts.tokens[start:ts.pos])


def parse_fn_type(ts: TokenStream) -> str:
    """
    Parse a function reference type 'fn(A, B) -> T' into its canonical
    name 'fn(A,B)->T', with comma-separated parameter types and an
    optional return type.
    """
    ts.expect("kw", "fn")
    ts.expect("sym", "(")

    params = []
    while ts.peek().value != ")":
        if params:
            ts.expect("sym", ",")

        params.append(parse_type(ts))
    ts.expect("sym", ")")

    name = f"fn({','.join(params)})"
    if ts.peek().value == "->":
        ts.next()
        name += f"->{parse_type(ts)}"

    return name
