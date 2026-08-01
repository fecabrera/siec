"""Feature tests for the builtin ``Slot<T>`` raw-storage abstraction."""


RESOURCE = r"""
@extern fn printf(format: char*, ...);

struct Resource: Destroy, Clone { id: i32; }

fn Resource::init(&self, id: i32) { self.id = id; }
fn Resource::destroy(&self) { printf("drop %d\n", self.id); }
fn Resource::clone(const &self) -> Resource {
    printf("clone %d\n", self.id);
    return Resource(self.id);
}
"""


def test_slot_has_element_layout_and_scalar_transitions(run):
    """A slot occupies T's storage and supports every raw state transition."""
    result = run(r"""
    fn main() -> i32 {
        if (@sizeof(Slot<i64>) != @sizeof(i64)) return 1;

        let slot: Slot<i64>;
        slot.write(40);
        if (slot.get() != 40) return 2;

        slot.replace(41);
        slot.get_mut() += 1;
        let value = slot.take();
        return value as i32;
    }
    """)
    assert result.returncode == 42


def test_slot_transfers_and_destroys_owned_values_exactly_once(run):
    """Write, replace, take, and drop preserve one cleanup responsibility."""
    result = run(RESOURCE + r"""
    fn main() -> i32 {
        let first: Slot<Resource>;
        first.write(Resource(1));
        first.replace(Resource(2));
        let value = first.take();

        let second: Slot<Resource>;
        second.write(Resource(3));
        second.drop();
        return value.id - 2;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop 1\ndrop 3\ndrop 2\n"


def test_slot_write_from_clones_owned_values(run):
    """Borrowed initialization clones an owner while copying plain types."""
    result = run(RESOURCE + r"""
    fn main() -> i32 {
        let source = Resource(7);
        let slot: Slot<Resource>;
        slot.write_from(source);
        slot.drop();

        let scalar: Slot<i32>;
        let value: i32 = 42;
        scalar.write_from(value);
        return scalar.get();
    }
    """)
    assert result.returncode == 42
    assert result.stdout == "clone 7\ndrop 7\ndrop 7\n"


def test_slot_write_from_rejects_owned_non_clone(compile_source):
    """A borrowed owner cannot initialize raw storage by shallow copy."""
    source = r"""
    struct Resource: Destroy {}
    fn Resource::destroy(&self) {}

    fn main() -> i32 {
        let source: Resource = {};
        let slot: Slot<Resource>;
        slot.write_from(source);
        return 0;
    }
    """
    try:
        compile_source(source)
    except TypeError as error:
        assert "implement Clone or use write" in str(error)
    else:
        raise AssertionError("borrowed Slot initialization shallow-copied")
