"""Feature tests for slice operators through GetSlice."""


def test_struct_slice_forms_select_get_slice_action(run):
    """Each optional-bound shape selects its corresponding action."""
    source = """
    struct Span: GetSlice<u64, u64> {}

    fn Span::get_slice(const &self) -> u64 { return 1; }
    fn Span::get_slice_from(const &self, start: const u64) -> u64 {
        return start + 10;
    }
    fn Span::get_slice_to(const &self, finish: const u64) -> u64 {
        return finish + 20;
    }
    fn Span::get_slice(const &self, start: const u64,
                       finish: const u64) -> u64 {
        return finish - start;
    }

    fn main() -> i32 {
        let span: Span = {};
        let total = span[:] + span[5:] + span[:7] + span[3:9];
        return total as i32 - 49;
    }
    """
    assert run(source).returncode == 0
