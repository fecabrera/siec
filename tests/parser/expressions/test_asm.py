"""Tests for parsing inline '@asm' blocks."""

from siec.ast import AsmBlock
from siec.parser.expressions import parse_expression


def test_asm_block_forms(ts):
    """
    Operands, clobbers, and the return type are all optional.
    """
    assert parse_expression(ts("@asm { nop }")) == AsmBlock(" nop ")

    assert parse_expression(ts("@asm (x, y) { nop }")) == AsmBlock(
        " nop ", ["x", "y"])

    assert parse_expression(ts('@asm @clobbers("x0", "memory") { nop }')) == AsmBlock(
        " nop ", [], None, ["x0", "memory"])

    assert parse_expression(ts("@asm (x) -> i32 { add $out, $x }")) == AsmBlock(
        " add $out, $x ", ["x"], "i32")


def test_asm_block_with_everything(ts):
    """
    Clobbers, operands, and a return type stack in order.
    """
    assert parse_expression(
        ts('@asm @clobbers("x9") (x, y) -> i64 { body }')) == AsmBlock(
        " body ", ["x", "y"], "i64", ["x9"])
