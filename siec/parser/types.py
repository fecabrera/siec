"""Parsing of type annotations."""

from siec.lexer.token import int_value
from siec.parser.stream import TokenStream


def parse_type(ts: TokenStream) -> str:
    """
    Parse a type annotation, including pointer and array suffixes
    (e.g. 'u8**', 'char*[]', 'char[][]').
    """
    # 'fn(A, B) -> T' is a function reference type; anything else is a base
    # type name; either may be followed by any mix of '*'s (which the lexer
    # may have glued into '**' tokens) and '[]' or '[N]' suffixes
    if ts.peek().value == "fn":
        name = parse_fn_type(ts)
    else:
        name = ts.expect("ident").value

    while True:
        if ts.peek().value in ("*", "**"):
            name += ts.next().value
        elif ts.peek().value == "[":
            ts.next()

            if ts.peek().value != "]":
                # normalize the size to decimal, so '[0x10]' and '[16]' agree
                size = int_value(ts.expect("int").value)
                ts.expect("sym", "]")
                name += f"[{size}]"
            else:
                ts.expect("sym", "]")
                name += "[]"
        else:
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
