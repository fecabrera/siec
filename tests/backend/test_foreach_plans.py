"""Feature tests for checked foreach plans."""


def test_foreach_protocol_is_selected_before_emission(
        monkeypatch, compile_source):
    """Emit consumes complete plans for iterable and iterator sources."""
    from siec.codegen import methods, statements
    from siec.codegen.hir import checked_foreach

    original_getter = methods.iteration_getter
    original_resolve = methods.resolve_method
    original_emit = statements.emit_foreach
    selected = {"getters": 0, "methods": 0, "plans": []}

    def iteration_getter(gen, *args, **kwargs):
        assert not gen.emitting
        selected["getters"] += 1
        return original_getter(gen, *args, **kwargs)

    def resolve_method(gen, *args, **kwargs):
        assert not gen.emitting
        selected["methods"] += 1
        return original_resolve(gen, *args, **kwargs)

    def emit_foreach(gen, builder, stmt, scope):
        plan = checked_foreach(stmt)
        assert plan is not None
        assert plan.has_next_symbol == plan.has_next_call.resolved_symbol
        assert plan.next_symbol == plan.next_call.resolved_symbol
        assert plan.element_reference_type.startswith(("&", "const &"))
        selected["plans"].append(plan)
        return original_emit(gen, builder, stmt, scope)

    monkeypatch.setattr(methods, "iteration_getter", iteration_getter)
    monkeypatch.setattr(methods, "resolve_method", resolve_method)
    monkeypatch.setattr(statements, "emit_foreach", emit_foreach)

    compile_source("""
    fn scan(values: const &i32[]) -> i32 {
        let total = 0;
        foreach (value : values) total += value;
        return total;
    }

    fn main() -> i32 {
        let values: i32[] = [20, 22];
        foreach (value : values) value += 1;

        let iterator = values.iterator();
        foreach (value : iterator) value -= 1;

        return scan(values);
    }
    """)

    assert selected["getters"] > 0
    assert selected["methods"] > 0
    assert len(selected["plans"]) == 3
    assert sum(plan.iterator_call is None
               for plan in selected["plans"]) == 1
