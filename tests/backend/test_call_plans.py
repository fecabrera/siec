"""Feature tests for checked call plans."""


def test_call_targets_are_selected_before_emission(
        monkeypatch, compile_source):
    """Direct, method, and constructor calls do not resolve during Emit."""
    from siec.codegen import generics, methods

    original_pick = generics.pick_call_candidate
    original_resolve_method = methods.resolve_method
    selected = {"calls": 0, "methods": 0}

    def pick_call_candidate(gen, *args, **kwargs):
        assert not gen.emitting
        selected["calls"] += 1
        return original_pick(gen, *args, **kwargs)

    def resolve_method(gen, *args, **kwargs):
        assert not gen.emitting
        selected["methods"] += 1
        return original_resolve_method(gen, *args, **kwargs)

    monkeypatch.setattr(generics, "pick_call_candidate", pick_call_candidate)
    monkeypatch.setattr(methods, "resolve_method", resolve_method)

    compile_source("""
    struct Box {
        value: i32;

        fn init(&self, value: i32) {
            self.value = value;
        }

        fn add(const &self, value: i32) -> i32 {
            return self.value + value;
        }
    }

    fn choose(value: i32) -> i32 { return value; }
    fn choose(value: i64) -> i32 { return value as i32; }
    fn increment(value: i32) -> i32 { return value + 1; }

    fn main() -> i32 {
        let box = Box(39);
        let callback: fn(i32) -> i32 = increment;
        return choose(box.add(callback(1)));
    }
    """)

    assert selected["calls"] > 0
    assert selected["methods"] > 0
