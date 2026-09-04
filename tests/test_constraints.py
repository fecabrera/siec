"""Tests for shared generic constraint operations."""

from siec.constraints import merge_constraints


def test_merge_constraints_mutates_and_normalizes_target():
    """Constraint merging sorts, deduplicates, and retains scalar bounds."""
    target = {
        "T": ("Last", "First"),
        "U": "Only",
    }

    merged = merge_constraints(target, {
        "T": ("Middle", "First"),
        "U": "Only",
        "V": "Added",
    })

    assert merged is target
    assert merged == {
        "T": ("First", "Last", "Middle"),
        "U": "Only",
        "V": "Added",
    }
