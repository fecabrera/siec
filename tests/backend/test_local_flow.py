"""Local data flow rejects uninitialized reads, repeated moves, and bad returns."""

import pytest


OWNER = """
@static let drops: i32 = 0;
struct Resource: Destroy { value: i32; }
fn Resource::init(&self) { self.value = 1; }
fn Resource::destroy(&self) { drops += 1; }
fn consume(value: Resource) {}
"""


@pytest.mark.parametrize('body', [
    'let value: i32; return value;',
    'let value: i32; if (condition) value = 1; return value;',
    'let value: i32; while (condition) { value = 1; break; } return value;',
    'let value: i32; value += 1; return value;',
    'let value: i32; return value + 1;',
    'let value: i32; return inspect(value);',
])
def test_uninitialized_reads_are_rejected(compile_source, body):
    """A value must be initialized on every path that reaches a read."""
    with pytest.raises(TypeError, match='uninitialized'):
        compile_source('fn inspect(x: i32) -> i32 { return x; }'
                       + 'fn bad(condition: bool) -> i32 {' + body + '}')


@pytest.mark.parametrize('body', [
    'let value: i32; if (condition) value = 1; else value = 2; return value;',
    'let value: i32; if (condition) return 0; else value = 2; return value;',
    'let value: i32; { value = 2; } return value;',
    'let value: const i32; if (condition) value = 1; else value = 2; return value;',
])
def test_continuing_branches_establish_initialization(compile_source, body):
    """Only paths that continue to a read contribute to definite initialization."""
    compile_source('fn good(condition: bool) -> i32 {' + body + '}')


def test_partial_struct_read_is_rejected(compile_source):
    """Writing one field does not initialize another field or the whole object."""
    for result in ('value.y', 'inspect(value)'):
        with pytest.raises(TypeError, match='uninitialized'):
            compile_source('''
            struct Pair { x: i32; y: i32; }
            fn inspect(value: Pair) -> i32 { return value.y; }
            fn bad() -> i32 { let value: Pair; value.x = 1; return RESULT; }
            '''.replace('RESULT', result))


def test_nested_field_initialization_and_defaults(run):
    """Field writes and defaults establish exactly the fields they initialize."""
    assert run('''
    struct Inner { x: i32; y: i32 = 2; }
    struct Outer { item: Inner; z: i32; }
    fn main() -> i32 {
        let value: Outer;
        value.item.x = 10;
        value.z = 30;
        return value.item.x + value.item.y + value.z;
    }
    ''').returncode == 42


@pytest.mark.parametrize('loop', [
    'while (condition) { consume(move value); }',
    'for (let i = 0; i < 2; i += 1) { consume(move value); }',
    'foreach (i : [1, 2]) { consume(move value); }',
    'while (condition) { consume(value); continue; }',
    'while (condition) { if (condition) { consume(value); continue; } }',
    'for (let i = 0; i < 2; consume(value)) { i += 1; }',
    'while (condition) { consume(value); if (condition) break; }',
])
def test_repeated_loop_consumption_is_rejected(compile_source, loop):
    """Every loop back edge must retain a valid source for the next consumption."""
    with pytest.raises(TypeError, match='moved'):
        compile_source(OWNER + 'fn bad(condition: bool) { let value = Resource();'
                       + loop + '}')


@pytest.mark.parametrize('exit_statement', ['break;', 'return;'])
def test_move_on_an_exiting_path_is_valid(compile_source, exit_statement):
    """A move on a path with no back edge is not repeated."""
    compile_source(OWNER + '''
    fn good(condition: bool) {
        let value = Resource();
        while (condition) { consume(move value); EXIT }
    }
    '''.replace('EXIT', exit_statement))


def test_loop_reinitialization_and_continue(run):
    """Reinitialization before consumption supplies one owner per iteration."""
    assert run(OWNER + '''
    fn main() -> i32 {
        let value: Resource;
        for (let i = 0; i < 3; i += 1) {
            value = Resource();
            consume(move value);
            continue;
        }
        return drops;
    }
    ''').returncode == 3


def test_const_initialization_cannot_repeat_in_loop(compile_source):
    """Possible initialization across a back edge prevents another const write."""
    with pytest.raises(TypeError, match='const'):
        compile_source('''
        fn bad(condition: bool) {
            let value: const i32;
            while (condition) { value = 1; }
        }
        ''')


@pytest.mark.parametrize('source', [
    'fn bad(source: &i32) -> &i32 { let local = 1; return local; }',
    'fn bad(source: &i32, value: i32) -> &i32 { return value; }',
    'fn bad(source: &i32, other: &i32) -> &i32 { return other; }',
    'fn bad(source: &i32) -> &i32 { let local = source; return local; }',
    'fn id(source: &i32) -> &i32 { return source; } '
    'fn bad(source: &i32) -> &i32 { let local = 1; return id(local); }',
    'struct Item { value: i32; } '
    'fn bad(source: &Item) -> &i32 { let local: Item = {1}; return local.value; }',
])
def test_reference_returns_reject_expired_or_unrelated_sources(compile_source, source):
    """Reference results must follow the function's first reference input."""
    with pytest.raises(TypeError, match='reference return'):
        compile_source(source)


@pytest.mark.parametrize('source', [
    'fn bad(source: const &i32) -> &i32 { return source; }',
    'struct Item { value: const i32; } '
    'fn bad(source: &Item) -> &i32 { return source.value; }',
    'fn id(source: const &i32) -> const &i32 { return source; } '
    'fn bad(source: const &i32) -> &i32 { return id(source); }',
])
def test_reference_returns_preserve_const(compile_source, source):
    """A reference return cannot grant mutation through const storage."""
    with pytest.raises(TypeError, match='const'):
        compile_source(source)


def test_reference_returns_follow_nested_calls_fields_and_indices(run):
    """Valid caller-owned places keep their origin across nested calls."""
    assert run('''
    struct Item { value: i32; }
    fn id(source: &i32) -> &i32 { return source; }
    fn field(source: &Item) -> &i32 { return id(source.value); }
    fn at(source: const &i32[], index: u64) -> const &i32 { return source[index]; }
    fn main() -> i32 {
        let item: Item = {0};
        field(item) = 40;
        let values = [2];
        return item.value + at(values, 0);
    }
    ''').returncode == 42


@pytest.mark.parametrize('body', [
    'let value: i32; defer inspect(value); value = 1;',
    'let value: i32; defer inspect(value); if (condition) value = 1; else value = 2;',
    'let value: i32; while (true) { value = 1; break; } inspect(value);',
])
def test_initialization_at_structured_exits(compile_source, body):
    """Deferred reads run at exit and an unconditional break can establish a value."""
    compile_source('fn inspect(value: i32) {} fn good(condition: bool) {' + body + '}')


@pytest.mark.parametrize('body', [
    'let value: i32; defer inspect(value);',
    'let value: i32; defer inspect(value); if (condition) return; value = 1;',
    'while (condition) { let value: i32; defer inspect(value); continue; }',
])
def test_deferred_reads_require_initialization_on_each_exit(compile_source, body):
    """Return and continue run deferred reads even when later writes are skipped."""
    with pytest.raises(TypeError, match='uninitialized'):
        compile_source('fn inspect(value: i32) {} fn bad(condition: bool) {' + body + '}')


def test_block_expression_merges_emitting_paths(compile_source):
    """An emit preserves writes made on every value-producing path."""
    compile_source('''
    fn good(condition: bool) -> i32 {
        let value: i32;
        let other: i32 = { if (condition) { value = 1; emit 2; }
                          else { value = 2; emit 3; } };
        return value + other;
    }
    ''')
    with pytest.raises(TypeError, match='uninitialized'):
        compile_source('''
        fn bad(condition: bool) -> i32 {
            let value: i32;
            let other: i32 = { if (condition) { value = 1; emit 2; }
                              else { emit 3; } };
            return value + other;
        }
        ''')


def test_loop_condition_consumption_is_checked(compile_source):
    """A condition executes again after the body and can repeat a move."""
    with pytest.raises(TypeError, match='moved'):
        compile_source(OWNER + '''
        fn test(value: Resource) -> bool { return true; }
        fn bad() { let value = Resource(); while (test(value)) {} }
        ''')


def test_nested_loop_continue_consumption_is_checked(compile_source):
    """A nested loop break does not remove the outer loop's back edge."""
    with pytest.raises(TypeError, match='moved'):
        compile_source(OWNER + '''
        fn bad(condition: bool) {
            let value = Resource();
            while (condition) {
                while (condition) { consume(value); break; }
                continue;
            }
        }
        ''')


def test_initializer_must_initialize_all_fields(compile_source):
    """A call named init cannot establish a value without initializing it."""
    with pytest.raises(TypeError, match='uninitialized'):
        compile_source('''
        struct Item { x: i32; y: i32; }
        fn Item::init(&self) { self.x = 1; }
        fn main() -> i32 { let value = Item(); return value.y; }
        ''')


def test_initializer_cannot_read_before_a_write(compile_source):
    """The receiver begins with only its default-initialized fields."""
    with pytest.raises(TypeError, match='uninitialized'):
        compile_source('''
        struct Item { value: i32; }
        fn Item::init(&self) { self.value += 1; }
        fn main() { let value = Item(); }
        ''')


def test_reference_assignment_keeps_the_storage_origin(run):
    """Writing caller storage does not turn it into local storage."""
    assert run('''
    fn set(source: &i32) -> &i32 { source = 42; return source; }
    fn main() -> i32 { let value = 0; return set(value); }
    ''').returncode == 42


def test_closure_body_checks_capture_initialization(compile_source):
    """A closure cannot read a capture that has no initialized value."""
    with pytest.raises(TypeError, match='uninitialized'):
        compile_source('''
        fn main() -> i32 {
            let value: i32;
            let callback = () -> i32 => value;
            return callback();
        }
        ''')


def test_returning_loop_path_does_not_move_the_continuing_owner(compile_source):
    """A loop-body return cannot invalidate the condition-false exit."""
    compile_source(OWNER + '''
    fn good(condition: bool) {
        let value = Resource();
        while (condition) { consume(value); return; }
        consume(value);
    }
    ''')


def test_break_skips_the_for_step(compile_source):
    """An unreachable step cannot consume an owner a second time."""
    compile_source(OWNER + '''
    fn good(condition: bool) {
        let value = Resource();
        for (let i = 0; condition; consume(value)) {
            consume(value);
            break;
        }
    }
    ''')


def test_macro_expansion_cannot_hide_a_loop_move(compile_source):
    """Resolved macro bodies participate in loop ownership analysis."""
    with pytest.raises(TypeError, match='moved'):
        compile_source(OWNER + '''
        @macro take(value) = consume(value);
        fn bad(condition: bool) {
            let value = Resource();
            while (condition) { take(value); }
        }
        ''')


def test_raw_array_reads_require_the_selected_element(compile_source):
    """A write to one raw element does not initialize the other elements."""
    with pytest.raises(TypeError, match='uninitialized'):
        compile_source('''
        fn bad() -> i32 {
            let values: @raw<i32>[2];
            values[0] = 1;
            return values[1];
        }
        ''')


def test_first_assignment_does_not_read_an_uninitialized_receiver(run):
    """Initial storage bypasses Assign even if the type has that method."""
    assert run('''
    struct Value: Assign<Value> { value: i32; }
    fn Value::assign(&self, value: Value) { self.value += value.value; }
    fn main() -> i32 { let value: Value; value = {42}; return value.value; }
    ''').returncode == 42


def test_shadowed_loop_binding_cannot_restore_a_moved_outer_owner(compile_source):
    """Continue carries the outer binding's move state through a shadowing local."""
    with pytest.raises(TypeError, match='moved'):
        compile_source(OWNER + '''
        fn bad(condition: bool) {
            let value = Resource();
            while (condition) {
                consume(value);
                let value = Resource();
                continue;
            }
        }
        ''')


def test_shadowing_does_not_initialize_a_deferred_capture(compile_source):
    """A defer retains its original binding when a later local has the same name."""
    with pytest.raises(TypeError, match='uninitialized'):
        compile_source('''
        fn inspect(value: i32) {}
        fn bad() {
            let value: i32;
            defer inspect(value);
            let value = 1;
        }
        ''')


def test_large_raw_array_partial_write_is_not_whole_initialization(compile_source):
    """Bounded field tracking must remain conservative for large raw layouts."""
    with pytest.raises(TypeError, match='uninitialized'):
        compile_source('''
        fn bad() -> i32 {
            let values: @raw<i32>[8192];
            values[0] = 1;
            return values[4096];
        }
        ''')


def test_tuple_parameter_pattern_preserves_loop_ownership(compile_source):
    """A pattern-bound parameter is still a local owner across loop iterations."""
    with pytest.raises(TypeError, match='moved'):
        compile_source(OWNER + '''
        fn bad((value, count): Tuple<Resource, i32>) {
            for (let i = 0; i < count; i += 1) { consume(value); }
        }
        ''')


def test_initializer_diagnostic_names_anonymous_union_storage(compile_source):
    """An incomplete variant constructor identifies its union and member names."""
    with pytest.raises(TypeError) as error:
        compile_source('''
        enum Kind { Integer, Bool }
        struct Value {
            kind: Kind;
            union { integer: i64; boolean: bool; };
        }
        fn Value::init(&self, kind: Kind) {
            self.kind = kind;
            case (kind) {
            when Kind::Integer: self.integer = 0;
            }
        }
        ''')
    assert str(error.value) == (
        "read of possibly uninitialized value 'self'; "
        "not initialized on every path: anonymous union in 'self' "
        "(members: integer, boolean)")


def test_initializer_diagnostic_names_missing_nested_fields(compile_source):
    """Only missing fields appear, in a stable order, with their full paths."""
    with pytest.raises(TypeError) as error:
        compile_source('''
        struct Pair { x: i32; y: i32; }
        struct Value { pair: Pair; count: i32; }
        fn Value::init(&self) { self.pair.x = 1; }
        ''')
    assert str(error.value) == (
        "read of possibly uninitialized value 'self'; "
        "not initialized on every path: 'self.count'; 'self.pair.y'")


def test_initializer_diagnostic_names_named_union_storage(compile_source):
    """A named union uses its source field name rather than anonymous wording."""
    with pytest.raises(TypeError) as error:
        compile_source('''
        union Payload { integer: i32; boolean: bool; }
        struct Value { payload: Payload; }
        fn Value::init(&self) {}
        ''')
    assert str(error.value) == (
        "read of possibly uninitialized value 'self'; "
        "not initialized on every path: union 'self.payload' "
        "(members: integer, boolean)")


def test_uninitialized_scalar_diagnostic_stays_concise(compile_source):
    """A scalar read needs no repeated storage description."""
    with pytest.raises(TypeError) as error:
        compile_source('fn bad() -> i32 { let value: i32; return value; }')
    assert str(error.value) == "read of possibly uninitialized value 'value'"
