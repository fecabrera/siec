"""Parsing of include directives."""

from siec.ast import Include
from siec.parser.stream import TokenStream


def parse_include(ts: TokenStream) -> Include:
    """
    Parse an '@include("path")' directive.
    """
    line = ts.peek().line
    ts.expect("sym", "@")
    ts.expect("ident", "include")
    ts.expect("sym", "(")
    path = ts.expect("str").value
    ts.expect("sym", ")")

    # a trailing ';' is fine, statement-style
    if ts.peek().syntax == ";":
        ts.next()

    return Include(path, line=line)
