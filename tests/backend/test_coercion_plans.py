"""Feature tests for checked coercion plans."""


def test_implicit_coercions_are_selected_before_emission(
        monkeypatch, compile_source):
    """Emit lowers checked coercions without testing source compatibility."""
    from siec.codegen import coercion, overloads

    original_parameter_fit = overloads.parameter_fit
    original_legacy = coercion._emit_coerced_legacy
    selected = {"fits": 0, "legacy": 0}

    def parameter_fit(gen, *args, **kwargs):
        assert not gen.emitting
        selected["fits"] += 1
        return original_parameter_fit(gen, *args, **kwargs)

    def emit_legacy(gen, *args, **kwargs):
        assert not gen.emitting
        selected["legacy"] += 1
        return original_legacy(gen, *args, **kwargs)

    monkeypatch.setattr(overloads, "parameter_fit", parameter_fit)
    monkeypatch.setattr(coercion, "_emit_coerced_legacy", emit_legacy)

    compile_source("""
    fn identity(value: i64) -> i64 { return value; }

    fn main() -> i32 {
        let signed: i8 = 1;
        let signed_wide: i64 = signed;
        let unsigned: u8 = 2;
        let unsigned_wide: u64 = unsigned;
        let narrow_float: f32 = 3.0;
        let wide_float: f64 = narrow_float;
        let array: i32[] = [4, 5];
        let data: !i32* = array;
        let erased: opaque* = data;
        let absent: i32* = null;
        let optional: Option<i64> = signed;
        if (optional != None) {
            let recovered: i64 = optional;
        }
        let callback: fn(i64) -> i64 = identity;
        return callback(signed_wide) as i32;
    }
    """)

    assert selected["fits"] > 0
    assert selected["legacy"] == 0
