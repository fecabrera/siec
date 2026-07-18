"""Feature tests for '@inline' functions."""

from siec.backend import prepare_module

SOURCE = """
@inline fn square(n: i32) -> i32 {
    return n * n;
}

fn main() -> i32 {
    return square(6) + square(2); // 40
}
"""


def test_inline_marks_the_function_alwaysinline(compile_source):
    """
    '@inline' lowers to LLVM's alwaysinline attribute.
    """
    assert "alwaysinline" in str(compile_source(SOURCE))


def test_inline_functions_inline_even_at_o0(compile_source):
    """
    Unlike C's hint, '@inline' guarantees inlining: no call remains in the
    caller even with no optimization requested.
    """
    # the inliner leaves llvm.lifetime intrinsic calls behind; what must be
    # gone is any call to square itself
    prepared = str(prepare_module(compile_source(SOURCE))[1])
    main_body = prepared.split("@main")[1].split("}")[0]
    assert "call" not in main_body.replace("call void @llvm.lifetime", "")


def test_inline_functions_behave_like_calls(run):
    """
    Inlining is invisible to the program's result.
    """
    assert run(SOURCE).returncode == 40
