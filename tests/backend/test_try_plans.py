"""Feature tests for checked try plans."""


def test_try_propagation_is_selected_before_emission(
        monkeypatch, compile_source):
    """Emit consumes checked Result arms and propagation actions."""
    from siec.codegen import checking, expressions
    from siec.codegen.hir import checked_try

    original_arms = checking.try_arms
    original_propagation = checking.check_try_propagation
    original_emit = expressions.emit_try
    selected = {"arms": 0, "propagations": 0, "plans": []}

    def try_arms(gen, *args, **kwargs):
        assert not gen.emitting
        selected["arms"] += 1
        return original_arms(gen, *args, **kwargs)

    def check_propagation(gen, *args, **kwargs):
        assert not gen.emitting
        selected["propagations"] += 1
        return original_propagation(gen, *args, **kwargs)

    def emit_try(gen, builder, expr, expected_type, scope):
        plan = checked_try(expr)
        assert plan is not None
        assert plan.result_type.startswith("Result<")
        assert plan.error_type == "u8"
        assert plan.ok_member is not None
        assert plan.error_member is not None
        if expr.propagates:
            assert plan.propagated_call is not None
            assert plan.propagated_return_type.startswith("Result<")
        else:
            assert plan.propagated_call is None
            assert plan.propagated_return_type is None
        selected["plans"].append(plan)
        return original_emit(gen, builder, expr, expected_type, scope)

    monkeypatch.setattr(checking, "try_arms", try_arms)
    monkeypatch.setattr(
        checking, "check_try_propagation", check_propagation)
    monkeypatch.setattr(expressions, "emit_try", emit_try)

    compile_source("""
    fn source(value: i32) -> Result<i32, u8> {
        if (value < 0) return Error(7);
        return Ok(value);
    }

    fn propagate(value: i32) -> Result<i32, u8> {
        let result = try source(value);
        return Ok(result);
    }

    fn discard(value: i32) -> Result<u8> {
        try source(value);
        return Ok();
    }

    fn fallback(value: i32) -> i32 {
        return try source(value) ?? 0;
    }

    fn main() -> i32 {
        propagate(1);
        discard(1);
        return fallback(42);
    }
    """)

    assert selected["arms"] == 3
    assert selected["propagations"] == 2
    assert len(selected["plans"]) == 3
