"""Parsing of type annotations."""

from .stream import TokenStream


def parse_type(ts: TokenStream) -> str:
    """
    Parse a type annotation, including pointer suffixes (e.g. 'u8**').
    """
    # 'fn(A, B) -> T' is a function reference type; anything else is a base
    # type name; either may be followed by '*'s, which the lexer may have
    # glued into '**' tokens
    if ts.peek().value == "fn":
        name = parse_fn_type(ts)
    else:
        name = ts.expect("ident").value

    while ts.peek().value in ("*", "**"):
        name += ts.next().value
    
    if ts.peek().value == "[":
        ts.next()

        if ts.peek().value != "]":
            size = ts.expect("int").value
            ts.expect("sym", "]")
            name += f"[{size}]"
        else:
            ts.expect("sym", "]")
            name += "[]"

    return name


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
