"""Parsing of include directives."""

from ..ast import Include
from .stream import TokenStream


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
