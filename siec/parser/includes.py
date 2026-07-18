"""Parsing of include directives."""

from siec.ast import Include
from siec.parser.stream import TokenStream


def parse_include(ts: TokenStream) -> Include:
    """
    Parse an '@include("path")' directive.
    """
    ts.expect("sym", "@")
    ts.expect("ident", "include")
    ts.expect("sym", "(")
    path = ts.expect("str").value
    ts.expect("sym", ")")
    return Include(path)
