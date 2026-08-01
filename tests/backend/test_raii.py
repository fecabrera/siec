"""Feature tests for nominal Destroy ownership and automatic cleanup."""


RESOURCE = r"""
@extern fn printf(format: char*, ...);

struct Resource: Destroy { id: i32; }

fn Resource::init(&self, id: i32) { self.id = id; }

fn Resource::destroy(&self) {
    printf("drop %d\n", self.id);
    self.id = -1;
}

fn inspect(value: const &Resource) {
    printf("use %d\n", value.id);
}

fn consume(value: Resource) {
    printf("consume %d\n", value.id);
}
"""


def test_owned_locals_join_defer_order_and_nested_scopes(run):
    """Automatic drops share each scope's reverse-ordered defer stack."""
    result = run(RESOURCE + r"""
    fn main() -> i32 {
        let outer = Resource(1);
        defer printf("defer\n");
        {
            let inner = Resource(2);
        }
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop 2\ndefer\ndrop 1\n"


def test_initialization_moves_cleanup_responsibility(run):
    """An owned initializer transfers its source's one cleanup obligation."""
    result = run(RESOURCE + r"""
    fn main() -> i32 {
        let first = Resource(42);
        let second = first;
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop 42\n"


def test_move_assignment_destroys_old_value_and_transfers_owner(run):
    """Built-in move replacement releases the target and disarms the source."""
    result = run(RESOURCE + r"""
    fn main() -> i32 {
        let first = Resource(1);
        let second = Resource(2);
        first = move second;
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop 1\ndrop 2\n"


def test_temporary_replacement_transfers_instead_of_dropping_twice(run):
    """A directly stored temporary becomes the target's cleanup obligation."""
    result = run(RESOURCE + r"""
    fn main() -> i32 {
        let value = Resource(1);
        value = Resource(2);
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop 1\ndrop 2\n"


def test_assign_owns_replacement_policy_and_receives_responsibility(run):
    """Assign consumes its source without compiler-side target destruction."""
    source = r"""
    @extern fn printf(format: char*, ...);

    struct Resource: Destroy, Assign<Resource> { id: i32; }
    fn Resource::init(&self, id: i32) { self.id = id; }
    fn Resource::destroy(&self) { printf("drop %d\n", self.id); }
    fn Resource::assign(&self, source: Resource) {
        printf("assign %d -> %d\n", self.id, source.id);
        self.id = source.id;
    }

    fn main() -> i32 {
        let first = Resource(1);
        let second = Resource(2);
        first = move second;
        return 0;
    }
    """
    result = run(source)
    assert result.returncode == 0
    assert result.stdout == "assign 1 -> 2\ndrop 2\n"


def test_temporaries_drop_after_borrow_or_owned_parameter_use(run):
    """Borrowed rvalues drop after the call; by-value ones drop in the callee."""
    result = run(RESOURCE + r"""
    fn main() -> i32 {
        inspect(Resource(1));
        consume(Resource(2));
        Resource(3);
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == (
        "use 1\ndrop 1\n"
        "consume 2\ndrop 2\n"
        "drop 3\n"
    )


def test_return_transfers_local_ownership_to_the_caller(run):
    """A returned local is disarmed and the receiving binding drops it once."""
    result = run(RESOURCE + r"""
    fn make() -> Resource {
        let value = Resource(42);
        return value;
    }

    fn main() -> i32 {
        let value = make();
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop 42\n"


def test_owned_parameters_and_branch_moves_drop_once(run):
    """Runtime ownership flags preserve the one-drop rule across branches."""
    result = run(RESOURCE + r"""
    fn choose(value: Resource, take: bool) {
        if (take) {
            consume(value);
        }
    }

    fn main() -> i32 {
        choose(Resource(1), true);
        choose(Resource(2), false);
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == (
        "consume 1\ndrop 1\n"
        "drop 2\n"
    )


def test_early_return_flushes_owned_scopes_inside_out(run):
    """Return uses the same cleanup stack as lexical fallthrough."""
    result = run(RESOURCE + r"""
    fn leave() {
        let outer = Resource(1);
        {
            let inner = Resource(2);
            return;
        }
    }

    fn main() -> i32 {
        leave();
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop 2\ndrop 1\n"


def test_nested_borrowed_temporary_lives_through_outer_call(run):
    """A nested borrowed temporary drops after the full call expression."""
    result = run(RESOURCE + r"""
    fn read(value: const &Resource) -> i32 {
        printf("read %d\n", value.id);
        return value.id;
    }

    fn main() -> i32 {
        printf("result %d\n", read(Resource(42)));
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "read 42\nresult 42\ndrop 42\n"


def test_temporary_member_base_drops_after_full_expression(run):
    """Taking a field from a constructed owner retains it through the use."""
    result = run(RESOURCE + r"""
    fn main() -> i32 {
        printf("id %d\n", Resource(7).id);
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "id 7\ndrop 7\n"


def test_condition_temporary_drops_before_entering_the_branch(run):
    """A condition is a full expression, including a temporary member base."""
    result = run(RESOURCE + r"""
    fn main() -> i32 {
        if (Resource(7).id) {
            printf("body\n");
        }
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop 7\nbody\n"


def test_owned_copy_without_clone_or_assign_from_is_rejected(compile_source):
    """Borrowed assignment cannot duplicate one cleanup responsibility."""
    source = RESOURCE + r"""
    fn main() -> i32 {
        let first = Resource(1);
        let second = Resource(2);
        first = second;
        return 0;
    }
    """
    try:
        compile_source(source)
    except TypeError as error:
        assert "cannot copy owned 'Resource' value" in str(error)
    else:
        raise AssertionError("owned copy was accepted")


def test_destroy_requires_a_mutable_receiver(compile_source):
    """Destroy cleanup must be able to invalidate the value it releases."""
    source = r"""
    struct Resource: Destroy {}
    fn Resource::destroy(const &self) {}
    fn main() -> i32 { return 0; }
    """
    try:
        compile_source(source)
    except TypeError as error:
        assert "receiver" in str(error)
    else:
        raise AssertionError("const Destroy receiver was accepted")


def test_manual_or_deferred_destroy_disarms_automatic_cleanup(run):
    """Existing explicit cleanup remains exactly-once after claiming Destroy."""
    direct = run(RESOURCE + r"""
    fn main() -> i32 {
        let value = Resource(1);
        value.destroy();
        return 0;
    }
    """)
    assert direct.returncode == 0
    assert direct.stdout == "drop 1\n"

    deferred = run(RESOURCE + r"""
    fn main() -> i32 {
        let value = Resource(2);
        defer value.destroy();
        return 0;
    }
    """)
    assert deferred.returncode == 0
    assert deferred.stdout == "drop 2\n"


def test_manual_destroy_invalidates_the_local(compile_source):
    """A manually released owner cannot be read before reinitialization."""
    source = RESOURCE + r"""
    fn main() -> i32 {
        let value = Resource(1);
        value.destroy();
        return value.id;
    }
    """
    try:
        compile_source(source)
    except TypeError as error:
        assert "use of moved value 'value'" in str(error)
    else:
        raise AssertionError("use after manual destroy was accepted")


def test_drop_statement_supports_tag_selected_field_cleanup(run):
    """A custom container may manually drop only its active owned field."""
    source = r"""
    @extern fn printf(format: char*, ...);

    struct Item: Destroy { id: i32; }
    fn Item::destroy(&self) { printf("item %d\n", self.id); }

    struct Tagged: Destroy {
        active: bool;
        item: Item;
    }

    fn Tagged::destroy(&self) {
        if (self.active) drop self.item;
        self.active = false;
    }

    fn main() -> i32 {
        let value: Tagged = { true, { 42 } };
        return 0;
    }
    """
    result = run(source)
    assert result.returncode == 0
    assert result.stdout == "item 42\n"


def test_drop_rejects_non_destroyable_places(compile_source):
    """Manual drop is an ownership operation, not an arbitrary method name."""
    try:
        compile_source("fn main() -> i32 { let n = 1; drop n; return 0; }")
    except TypeError as error:
        assert "does not implement Destroy" in str(error)
    else:
        raise AssertionError("a scalar drop was accepted")


def test_destroy_query_does_not_instantiate_unrelated_claim_types(
        compile_source):
    """Ownership checks inspect only Destroy claims, without type side effects."""
    compile_source(r"""
    interface Show { fn show(const &self); }
    interface Uses<T>;

    struct Box<T>: Show { value: T; }
    fn Box<T>::show(const &self) {
        self.value.missing();
    }

    @extend T[]: Uses<Box<T>>;

    fn variadic(args...) {}
    fn main() -> i32 { return 0; }
    """)


def test_reference_return_borrows_an_owned_container_element(compile_source):
    """Returning a reference to a field or item is not a partial move."""
    compile_source(r"""
    struct Resource: Destroy { id: i32; }
    fn Resource::destroy(&self) {}

    fn first(values: const &Resource[]) -> const &Resource {
        return values[0];
    }

    fn main() -> i32 { return 0; }
    """)


def test_const_value_return_is_a_non_owning_element_view(run):
    """A const by-value accessor does not duplicate cleanup responsibility."""
    source = r"""
    @extern fn printf(format: char*, ...);

    struct Resource: Destroy { id: i32; }
    fn Resource::destroy(&self) { printf("drop %d\n", self.id); }

    struct Owner: Destroy { item: Resource; }
    fn Owner::destroy(&self) { drop self.item; }
    fn Owner::get_item(const &self) -> const Resource {
        return self.item;
    }

    fn main() -> i32 {
        let owner: Owner = {{42}};
        let view = owner.get_item();
        printf("use %d\n", view.id);
        return 0;
    }
    """
    result = run(source)
    assert result.returncode == 0
    assert result.stdout == "use 42\ndrop 42\n"


def test_const_value_parameter_borrows_named_and_temporary_owners(run):
    """Const value parameters borrow; caller temporaries still drop after use."""
    result = run(RESOURCE + r"""
    fn inspect_value(value: const Resource) {
        printf("use %d\n", value.id);
    }

    fn main() -> i32 {
        let value = Resource(1);
        inspect_value(value);
        printf("again %d\n", value.id);
        inspect_value(Resource(2));
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == (
        "use 1\nagain 1\n"
        "use 2\ndrop 2\n"
        "drop 1\n"
    )
