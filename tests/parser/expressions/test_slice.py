"""Tests for parsing slice expressions."""

from siec.ast import Index, IntLiteral, Member, Slice, Var
from siec.parser.expressions import parse_primary


def test_slice_with_both_bounds(ts):
    """
    'arr[1:3]' parses to a Slice with both bounds.
    """
    assert parse_primary(ts("arr[1:3]")) == Slice(Var("arr"), IntLiteral(1), IntLiteral(3))


def test_slice_without_stop(ts):
    """
    'arr[1:]' leaves the stop bound as None.
    """
    assert parse_primary(ts("arr[1:]")) == Slice(Var("arr"), IntLiteral(1), None)


def test_slice_without_start(ts):
    """
    'arr[:3]' leaves the start bound as None.
    """
    assert parse_primary(ts("arr[:3]")) == Slice(Var("arr"), None, IntLiteral(3))


def test_slice_without_bounds(ts):
    """
    'arr[:]' leaves both bounds as None: the full view.
    """
    assert parse_primary(ts("arr[:]")) == Slice(Var("arr"), None, None)


def test_index_without_a_colon_stays_an_index(ts):
    """
    'arr[1]' still parses to an Index, not a Slice.
    """
    assert parse_primary(ts("arr[1]")) == Index(Var("arr"), IntLiteral(1))


def test_slice_chains_with_members(ts):
    """
    A slice takes its place in a postfix chain like indexing does.
    """
    assert parse_primary(ts("arr[1:].length")) == Member(
        Slice(Var("arr"), IntLiteral(1), None), "length")
