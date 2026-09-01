"""Feature tests for nominal Destroy ownership and automatic cleanup."""

import pytest


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


REFERENCE_COPY = r"""
@extern fn malloc(size: u64) -> opaque*;
@extern fn free(ptr: opaque*);

struct Buffer: Destroy, Clone { data: i32*; }

fn Buffer::init(&self, value: i32) {
    self.data = malloc(@sizeof(i32)) as i32*;
    self.data[0] = value;
}

fn Buffer::clone(const &self) -> Buffer {
    return Buffer(self.data[0]);
}

fn Buffer::destroy(&self) {
    free(self.data);
    self.data = null;
}

fn Buffer::touch(&self) -> &Buffer {
    self.data[0] += 1;
    return self;
}

fn Buffer::null_terminate(&self) -> self {
    self.data[0] = 1;
}

fn Buffer::a(&self) -> self {
    self.data[0] += 1;
}

fn Buffer::b(&self) -> self {
    self.data[0] += 2;
}

fn Buffer::c(&self) -> &Buffer {
    return self.a().b();
}

fn borrow(value: &Buffer) -> &Buffer {
    return value;
}

fn copy(value: &Buffer) -> Buffer {
    return borrow(value);
}

fn read(value: Buffer) -> i32 {
    return value.data[0];
}

fn terminated() -> Buffer {
    return Buffer(0).null_terminate();
}

fn main() -> i32 {
    let chained = Buffer(40).touch();
    let copied = borrow(chained);
    let returned = copy(chained);
    let assigned = Buffer(0);
    assigned = borrow(chained);
    let argument = read(borrow(chained));
    let named = Buffer(0);
    let named_copy = named.null_terminate();
    let built = Buffer(0).null_terminate().null_terminate();
    let built_returned = terminated();
    let built_assigned = Buffer(9);
    built_assigned = Buffer(0).null_terminate();
    let built_argument = read(Buffer(0).null_terminate());
    let discarded = Buffer(0);
    discarded.a().b();
    let referenced = Buffer(0);
    if (chained.data[0] != 41) { return 1; }
    if (copied.data[0] != 41 or copied.data == chained.data) { return 2; }
    if (returned.data[0] != 41 or returned.data == chained.data) { return 3; }
    if (assigned.data[0] != 41 or assigned.data == chained.data) { return 4; }
    if (argument != 41) { return 5; }
    if (named.data[0] != 1) { return 6; }
    if (named_copy.data[0] != 1 or named_copy.data == named.data) { return 7; }
    if (built.data[0] != 1) { return 8; }
    if (built_returned.data[0] != 1) { return 9; }
    if (built_assigned.data[0] != 1) { return 10; }
    if (built_argument != 1) { return 11; }
    if (discarded.data[0] != 3) { return 12; }
    if (referenced.c().data != referenced.data) { return 13; }
    if (referenced.data[0] != 3) { return 14; }
    let received = referenced.c();
    if (referenced.data[0] != 6) { return 15; }
    if (received.data[0] != 6 or received.data == referenced.data) { return 16; }
    return 42;
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


def test_result_destroys_only_its_active_owned_payload(run):
    """An owned Result drops its value or error according to its tag."""
    result = run(RESOURCE + r"""
    fn outcome(ok: bool) -> Result<Resource, Resource> {
        if (ok) { return Ok(Resource(1)); }
        return Error(Resource(2));
    }

    fn main() -> i32 {
        {
            let value = outcome(true);
        }
        {
            let error = outcome(false);
        }
        {
            let value: Result<Resource, i32> = Ok(Resource(3));
        }
        {
            let error: Result<i32, Resource> = Error(Resource(4));
        }
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop 1\ndrop 2\ndrop 3\ndrop 4\n"


def test_result_member_move_disarms_the_outer_result(run):
    """Moving a proven active member transfers the Result's one owner."""
    result = run(RESOURCE + r"""
    fn outcome(ok: bool) -> Result<Resource, Resource> {
        if (ok) { return Ok(Resource(1)); }
        return Error(Resource(2));
    }

    fn take(ok: bool) {
        let result = outcome(ok);
        if (not result) {
            let error = result.error;
            return;
        }
        let value = result.value;
    }

    fn main() -> i32 {
        take(false);
        take(true);
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop 2\ndrop 1\n"


def test_try_transfers_owned_value_or_error_without_a_second_drop(run):
    """Try gives ownership to its value or its except binding."""
    result = run(RESOURCE + r"""
    fn outcome(ok: bool) -> Result<Resource, Resource> {
        if (ok) { return Ok(Resource(1)); }
        return Error(Resource(2));
    }

    fn take(ok: bool) {
        let value = try outcome(ok) except (error) {
            return;
        }
    }

    fn main() -> i32 {
        take(false);
        take(true);
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop 2\ndrop 1\n"


def test_try_transfers_an_owned_error_from_valueless_result(run):
    """Try over Result<E> drops only a bound failed error."""
    result = run(RESOURCE + r"""
    fn outcome(ok: bool) -> Result<Resource> {
        if (ok) { return Ok(); }
        return Error(Resource(3));
    }

    fn take(ok: bool) {
        try outcome(ok) except (error) {
            return;
        }
    }

    fn main() -> i32 {
        take(false);
        take(true);
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop 3\n"


def test_result_assignment_and_member_return_transfer_once(run):
    """Replacement and a returned active member keep one cleanup owner."""
    result = run(RESOURCE + r"""
    fn outcome(ok: bool) -> Result<Resource, Resource> {
        if (ok) { return Ok(Resource(1)); }
        return Error(Resource(2));
    }

    fn take(ok: bool) -> Resource {
        let result = outcome(ok);
        if (not result) { return result.error; }
        return result.value;
    }

    fn main() -> i32 {
        let result = outcome(true);
        result = outcome(false);
        let error = take(false);
        let value = take(true);
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop 1\ndrop 1\ndrop 2\ndrop 2\n"


def test_bare_try_transfers_an_owned_error_to_the_caller(run):
    """Bare try drops a discarded value or propagates one owned error."""
    result = run(RESOURCE + r"""
    fn outcome(ok: bool) -> Result<Resource, Resource> {
        if (ok) { return Ok(Resource(1)); }
        return Error(Resource(2));
    }

    fn forward(ok: bool) -> Result<Resource> {
        try outcome(ok);
        return Ok();
    }

    fn main() -> i32 {
        let success = forward(true);
        let failure = forward(false);
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop 1\ndrop 2\n"


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


def test_operator_rewrite_preserves_temporary_ownership(run):
    """A desugared operator result transfers the source expression's owner."""
    source = RESOURCE + r"""
    @extend Resource: Add<Resource, i32>;

    fn Resource::add(&self, amount: const i32) -> Resource {
        return Resource(self.id + amount);
    }

    fn make_added() -> Resource {
        let base = Resource(40);
        return base + 2;
    }

    fn main() -> i32 {
        let base = Resource(10);
        let bound = base + 1;
        inspect(bound);
        inspect(base + 2);
        consume(base + 3);
        let returned = make_added();
        inspect(returned);
        return 0;
    }
    """
    result = run(source)
    assert result.returncode == 0
    assert result.stdout == (
        "use 11\n"
        "use 12\ndrop 12\n"
        "consume 13\ndrop 13\n"
        "drop 40\nuse 42\n"
        "drop 42\ndrop 11\ndrop 10\n"
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


def test_closure_argument_drops_its_own_temporaries(run):
    """A nested function's temporaries must not join the caller's drop frame."""
    result = run(RESOURCE + r"""
    fn invoke(callback: closure fn()) {
        callback();
    }

    fn main() -> i32 {
        invoke(() => {
            inspect(Resource(42));
        });
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "use 42\ndrop 42\n"


def test_outer_temporary_drops_after_call_with_closure_argument(run):
    """Outer borrowed temporaries still drop after the call that builds a closure."""
    result = run(RESOURCE + r"""
    fn take(value: const &Resource, callback: closure fn()) {
        inspect(value);
        callback();
    }

    fn main() -> i32 {
        take(Resource(1), () => {
            inspect(Resource(2));
        });
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "use 1\nuse 2\ndrop 2\ndrop 1\n"


def test_mutable_reference_mutates_the_owned_temporary(run):
    """Writes through '&T' must hit the same storage Destroy will drop."""
    result = run(RESOURCE + r"""
    fn bump(value: &Resource) {
        value.id += 1;
    }

    fn main() -> i32 {
        bump(Resource(41));
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop 42\n"


def test_mutable_reference_to_temporary_does_not_double_free(run):
    """Replacing a temporary's heap buffer must not free it twice."""
    result = run(r"""
    @extern fn malloc(size: u64) -> opaque*;
    @extern fn free(ptr: opaque*);
    @extern fn printf(format: char*, ...);

    struct Buffer: Destroy { ptr: opaque*; }

    fn Buffer::init(&self) {
        self.ptr = malloc(8);
    }

    fn Buffer::grow(&self) {
        free(self.ptr);
        self.ptr = malloc(16);
    }

    fn Buffer::destroy(&self) {
        free(self.ptr);
        self.ptr = null;
        printf("drop\n");
    }

    fn grow(value: &Buffer) {
        value.grow();
    }

    fn main() -> i32 {
        grow(Buffer());
        return 0;
    }
    """)
    assert result.returncode == 0
    assert result.stdout == "drop\n"


def test_owned_reference_copies_and_self_return_chains(run):
    """Owned copies clone, while discarded and borrowed self chains do not."""
    assert run(REFERENCE_COPY).returncode == 42


def test_jit_owned_reference_copies_and_self_return_chains(compile_source):
    """JIT follows the same owned-copy and self-chain rules as native code."""
    from siec.backend import run_jit

    assert run_jit(compile_source(REFERENCE_COPY), ["test.sie"]) == 42


def test_owned_reference_result_requires_clone(compile_source):
    """An owned borrowed value cannot become a second owner without Clone."""
    with pytest.raises(TypeError, match="cannot copy owned 'Buffer'.*Clone"):
        compile_source(r"""
        struct Buffer: Destroy { value: i32; }

        fn Buffer::destroy(&self) {}
        fn Buffer::view(&self) -> &Buffer { return self; }

        fn main() -> i32 {
            let source: Buffer = {42};
            let copy = source.view();
            return copy.value;
        }
        """)


def test_self_return_requires_a_mutable_receiver(compile_source):
    """The self contract is available only to mutable instance methods."""
    with pytest.raises(SyntaxError, match="only a method can return self"):
        compile_source("fn make() -> self {}")

    with pytest.raises(SyntaxError, match="mutable '&self' receiver"):
        compile_source(r"""
        struct S {}
        fn S::view(const &self) -> self {}
        """)


def test_self_return_cannot_return_another_value(compile_source):
    """Every explicit exit from a self-returning method returns its receiver."""
    with pytest.raises(TypeError, match="can only return self"):
        compile_source(r"""
        struct S {}
        fn S::replace(&self, other: &S) -> self { return other; }
        """)


def test_self_returned_temporary_does_not_require_clone(run):
    """A temporary receiver keeps its one owner through a builder call."""
    result = run(r"""
    struct Value: Destroy { number: i32; }

    fn Value::init(&self, number: i32) { self.number = number; }
    fn Value::destroy(&self) {}
    fn Value::add(&self, amount: i32) -> self {
        self.number += amount;
    }

    fn main() -> i32 {
        let value = Value(40).add(2);
        return value.number;
    }
    """)
    assert result.returncode == 42


def test_self_returned_named_receiver_still_requires_clone(compile_source):
    """A named receiver remains owned, so a second owner needs Clone."""
    with pytest.raises(TypeError, match="cannot copy owned 'Value'.*Clone"):
        compile_source(r"""
        struct Value: Destroy { number: i32; }

        fn Value::destroy(&self) {}
        fn Value::add(&self, amount: i32) -> self {
            self.number += amount;
        }

        fn main() -> i32 {
            let first: Value = {40};
            let second = first.add(2);
            return second.number;
        }
        """)


def test_generic_self_return_keeps_the_instantiated_receiver(run):
    """Generic builder methods return their concrete receiver instance."""
    result = run(r"""
    struct Box<T>: Destroy { value: T; }

    fn Box<T>::init(&self, value: T) { self.value = value; }
    fn Box<T>::destroy(&self) {}
    fn Box<T>::replace(&self, value: T) -> self {
        self.value = value;
    }

    fn main() -> i32 {
        let box = Box<i32>(1).replace(42);
        return box.value;
    }
    """)
    assert result.returncode == 42


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
