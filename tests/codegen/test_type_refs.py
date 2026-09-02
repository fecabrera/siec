"""Tests for the shared structural type representation."""

import pytest

from siec.codegen.type_refs import derivation, parse_type_ref


@pytest.mark.parametrize("spelling", [
    "i32",
    "const !i32*",
    "char*[]",
    "i32[COUNT+1]",
    "raw<Tuple<i32,i32>>[4]*",
    "Tuple<i32,fn(i8)->bool>[]",
    "fn(fn()->i32,i8)->fn()",
    "fn()*[]",
    "closure fn(Map<i32,i32>,i32)->i32",
    "struct{x:i32;y:Tuple<i8,i8>}*",
    "union{value:i32;bytes:raw<u8>[4]}",
])
def test_type_ref_round_trips_canonical_spelling(spelling):
    """All supported nested type forms retain their canonical spelling."""
    assert parse_type_ref(spelling).spelling() == spelling


def test_type_ref_parses_each_spelling_once():
    """All phases share the cached object for one canonical spelling."""
    assert parse_type_ref("Tuple<i32,i32>*") is parse_type_ref(
        "Tuple<i32,i32>*")


def test_derivation_separates_the_base_from_ordered_suffixes():
    """Pointer and array derivations retain their source order."""
    base, suffix = derivation(parse_type_ref("Tuple<i32,i32>*[][4]"))
    assert base.spelling() == "Tuple<i32,i32>"
    assert suffix == "*[][4]"


@pytest.mark.parametrize("spelling", [
    "Tuple<i32",
    "raw<i32>",
    "struct{x:i32",
    "fn(i32",
])
def test_type_ref_rejects_malformed_nesting(spelling):
    """Malformed nested types fail in the shared parser."""
    with pytest.raises(TypeError, match="malformed"):
        parse_type_ref(spelling)
