"""Const access preserves ownership across bindings, calls, and returns."""

import pytest


RESOURCE = r"""
@extern fn printf(format: char*, ...);

struct Resource: Destroy, Clone { id: i32; }
fn Resource::init(&self, id: i32) { self.id = id; }
fn Resource::destroy(&self) { printf("drop %d\n", self.id); }
fn Resource::clone(const &self) -> Resource {
    printf("clone %d\n", self.id);
    return Resource(self.id);
}
fn inspect(value: const &Resource) { printf("use %d\n", value.id); }
"""


def test_const_owners_move_clone_and_clean_up(run):
    """Const bindings own constructors, transferred values, and clones."""
    result = run(RESOURCE + r"""
    fn main() {
        let first = Resource(1);
        let second: const Resource = move first;
        let third: const Resource = second.clone();
        let fourth: const Resource = move second;
        inspect(fourth);
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "clone 1\nuse 1\ndrop 1\ndrop 1\n"


def test_const_parameters_and_returns_transfer_cleanup(run):
    """A const value return transfers cleanup through a const parameter."""
    result = run(RESOURCE + r"""
    fn forward(value: const Resource) -> const Resource { return value; }
    fn consume(value: const Resource) { inspect(value); }
    fn main() {
        let value: const Resource = forward(Resource(2));
        consume(value);
        printf("after\n");
        consume(forward(Resource(3)));
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "use 2\ndrop 2\nafter\nuse 3\ndrop 3\n"


def test_const_return_temporary_is_destroyed_after_borrow(run):
    """A const result borrowed by a call stays alive through that call."""
    result = run(RESOURCE + r"""
    fn make() -> const Resource { return Resource(4); }
    fn main() { inspect(make()); printf("after\n"); }
    """)
    assert result.returncode == 0
    assert result.stdout == "use 4\ndrop 4\nafter\n"


def test_reference_parameter_copy_clones_without_consuming_borrow(run):
    """Copying a reference parameter creates an owner and preserves its source."""
    result = run(RESOURCE + r"""
    fn copy(value: const &Resource) -> const Resource { return value; }
    fn main() {
        let value: const Resource = Resource(5);
        let other = copy(value);
        inspect(value);
        inspect(other);
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "clone 5\nuse 5\nuse 5\ndrop 5\ndrop 5\n"


def test_const_parameter_through_function_value_and_closure(run):
    """Indirect functions and closures own their const value arguments."""
    result = run(RESOURCE + r"""
    fn consume(value: const Resource) { inspect(value); }
    fn main() {
        let indirect = consume;
        indirect(Resource(6));
        let callback = (value: const Resource) => { inspect(value); };
        callback(Resource(7));
        printf("after\n");
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "use 6\ndrop 6\nuse 7\ndrop 7\nafter\n"


def test_erased_resource_can_be_borrowed_or_explicitly_cloned(run):
    """Erased access aliases the payload; explicit clone creates a new owner."""
    result = run(RESOURCE + r"""
    fn main() {
        let value: const Resource = Resource(8);
        let erased = value as Any;
        inspect(erased as const Resource);
        let copy: const Resource = (erased as const Resource).clone();
        inspect(value);
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "use 8\nclone 8\nuse 8\ndrop 8\ndrop 8\n"


@pytest.mark.parametrize("body", [
    "let value = Resource(1); let other: const Resource = value; inspect(value);",
    "let value = Resource(1); consume(value); inspect(value);",
    "let value: const Resource = Resource(1); let other = move value; inspect(value);",
])
def test_const_transfer_invalidates_the_source(compile_source, body):
    """Const destinations and parameters do not leave a second owner alive."""
    with pytest.raises(TypeError, match="moved value"):
        compile_source(RESOURCE + "fn consume(value: const Resource) {}"
                       + "fn main() {" + body + "}")


@pytest.mark.parametrize("body", [
    "value.id = 2;",
    "value.destroy();",
    "drop value;",
])
def test_const_ownership_does_not_grant_mutable_access(compile_source, body):
    """Only automatic cleanup bypasses a const owner's mutation restriction."""
    with pytest.raises(TypeError, match="const"):
        compile_source(RESOURCE + "fn main() {"
                       + "let value: const Resource = Resource(1);"
                       + body + "}")


def test_erased_resource_cannot_become_an_owner_by_cast(compile_source):
    """An erased descriptor cannot create an owner by shallow copy."""
    with pytest.raises(TypeError, match="borrow through a reference"):
        compile_source(RESOURCE + """
        fn main() {
            let value = Resource(1);
            let erased = value as Any;
            let other = erased as const Resource;
        }
        """)


def test_const_member_value_return_is_not_a_borrow(compile_source):
    """An accessor must return a reference or explicitly clone its field."""
    with pytest.raises(TypeError, match="cannot move part"):
        compile_source(RESOURCE + """
        struct Owner: Destroy { value: Resource; }
        fn Owner::destroy(&self) { drop self.value; }
        fn Owner::get(const &self) -> const Resource { return self.value; }
        """)


def test_const_generic_slot_and_option_preserve_ownership(run):
    """Const owners retain cleanup through generic slots and tagged storage."""
    result = run(RESOURCE + r"""
    fn main() {
        let source: const Resource = Resource(9);
        let slot: Slot<Resource>;
        slot.write(source);
        let value: const Resource = slot.take();
        inspect(value);
        let option: const Option<Resource> = Resource(10);
        printf("after\n");
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "use 9\nafter\ndrop 10\ndrop 9\n"


def test_const_owner_aggregate_transfers_field_cleanup(run):
    """A const aggregate retains its owned fields until its own cleanup."""
    result = run(RESOURCE + r"""
    struct Holder: Destroy { item: Resource; }
    fn Holder::destroy(&self) { drop self.item; }
    fn main() {
        let value: const Resource = Resource(11);
        let owner: const Holder = {value};
        let temporary: const Holder = {Resource(12)};
        inspect(owner.item);
        printf("after\n");
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "use 11\nafter\ndrop 12\ndrop 11\n"


def test_reference_getter_clones_owned_index_values(run):
    """Index syntax borrows for calls and clones when it creates an owner."""
    result = run(RESOURCE + r"""
    struct Holder: Destroy, GetItem<i32, Resource> { item: Resource; }
    fn Holder::destroy(&self) { drop self.item; }
    fn Holder::get_item(const &self, key: const i32) -> const &Resource {
        return self.item;
    }
    fn main() {
        let owner: Holder = {{13}};
        inspect(owner[0]);
        let value: const Resource = owner[0];
        inspect(owner[0]);
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "use 13\nclone 13\nuse 13\ndrop 13\ndrop 13\n"


def test_const_generic_return_and_deferred_initialization(run):
    """Generic const returns retain ownership through one-time initialization."""
    result = run(RESOURCE + r"""
    fn identity<T>(value: const T) -> const T { return value; }
    fn main() {
        let value: const Resource;
        value = identity(Resource(14));
        inspect(value);
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "use 14\ndrop 14\n"


def test_const_result_transfers_only_its_active_payload(run):
    """A const Result keeps one cleanup obligation when its payload moves."""
    result = run(RESOURCE + r"""
    fn make() -> const Result<Resource, i32> { return Ok(Resource(15)); }
    fn main() {
        let result = make();
        if (result.ok) {
            let value: const Resource = result.value;
            inspect(value);
        }
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "use 15\ndrop 15\n"


@pytest.mark.parametrize("condition", ["true", "false"])
def test_const_conditional_owner_transfers_only_the_selected_source(run, condition):
    """Conditional const initialization leaves one cleanup per resource."""
    result = run(RESOURCE + """
    fn main() {
        let first: const Resource = Resource(16);
        let second: const Resource = Resource(17);
        let selected: const Resource = CONDITION ? first : second;
        inspect(selected);
    }
    """.replace("CONDITION", condition))
    assert result.returncode == 0
    assert result.stdout == (
        "use 16\ndrop 16\ndrop 17\n" if condition == "true"
        else "use 17\ndrop 17\ndrop 16\n")


def test_conditional_borrowed_resource_clones_the_selected_value(run):
    """A conditional value formed from references creates an owned clone."""
    result = run(RESOURCE + r"""
    fn choose(a: const &Resource, b: const &Resource) -> const Resource {
        return true ? a : b;
    }
    fn main() {
        let a = Resource(18);
        let b = Resource(19);
        let selected = choose(a, b);
        inspect(a);
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "clone 18\nuse 18\ndrop 18\ndrop 19\ndrop 18\n"
