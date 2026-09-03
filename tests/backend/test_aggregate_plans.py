"""Feature tests for checked aggregate field plans."""


def test_aggregate_fields_are_selected_before_emission(
        monkeypatch, compile_source):
    """Runtime and static emission consume checked aggregate mappings."""
    from siec.codegen import aggregates, globals as global_codegen

    original = aggregates.resolve_aggregate
    selected = {"count": 0}

    def resolve_aggregate(gen, *args, **kwargs):
        assert not gen.emitting
        selected["count"] += 1
        return original(gen, *args, **kwargs)

    monkeypatch.setattr(aggregates, "resolve_aggregate", resolve_aggregate)
    monkeypatch.setattr(
        global_codegen, "resolve_aggregate", resolve_aggregate)

    compile_source("""
    struct Pair {
        left: i32 = 40;
        right: i32 = 2;
    }

    union Number {
        signed: i64;
        unsigned: u64;
    }

    @static let static_pair: Pair = { right = 2 };
    @static let static_number: Number = { signed = 42 };

    fn main() -> i32 {
        let pair: Pair = { right = 2 };
        let number: Number = { signed = 42 };
        let values: i32[] = [40, 2];
        let view: i32[] = { length = 2, data = values.data };
        return pair.left + view.length as i32;
    }
    """)

    assert selected["count"] >= 5


def test_static_named_aggregate_uses_declared_defaults(run):
    """An omitted static field takes the same default as a runtime field."""
    source = """
    struct Config {
        enabled: bool = true;
        value: i32 = 40;
        offset: i32;
    }

    @static let config: Config = { offset = 2 };

    fn main() -> i32 {
        if (not config.enabled) return 1;
        return config.value + config.offset;
    }
    """
    assert run(source).returncode == 42
