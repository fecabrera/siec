"""Feature tests for indexed operators through GetItem and SetItem."""

import pytest


def test_struct_indexing_desugars_to_item_methods(run):
    """
    A struct read and write through [] call get_item and set_item, while
    their key and value types come from the methods' ordinary signatures.
    """
    source = """
    struct Table: GetItem<u64, i32>, SetItem<u64, i32> {
        values: i32[];
    }

    fn Table::get_item(const &self, key: u64) -> i32 {
        return self.values[key];
    }

    fn Table::set_item(&self, key: u64, value: i32) {
        self.values[key] = value;
    }

    fn main() -> i32 {
        let values: i32[] = [10, 20, 30];
        let table: Table = { values };

        table[1] = 40;
        table[0] += 2;
        return table[0] + table[1] - 10;
    }
    """
    assert run(source).returncode == 42


def test_get_item_result_drives_inference(run):
    """
    The type of an indexed struct expression is get_item's return type.
    """
    source = """
    struct Lookup: GetItem<u64, i64> {
        value: i64;
    }

    fn Lookup::get_item(const &self, key: u64) -> i64 {
        return self.value + key as i64;
    }

    fn main() -> i32 {
        let lookup: Lookup = { 40 };
        let found = lookup[2];
        return found as i32;
    }
    """
    assert run(source).returncode == 42


def test_compound_item_assignment_sets_a_struct_value_back(run):
    """
    A copied item uses its binary operator and is passed to set_item;
    its own in-place operator must not try to mutate the temporary.
    """
    source = """
    struct Number: Add<Number, Number>, AddAssign<Number> {
        value: i32;
    }

    fn Number::add(&self, other: Number) -> Number {
        let result: Number = { self.value + other.value };
        return result;
    }

    fn Number::add_assign(&self, other: Number) {
        self.value += other.value;
    }

    struct Box: GetItem<u64, Number>, SetItem<u64, Number> {
        value: Number;
    }

    fn Box::get_item(const &self, key: u64) -> Number {
        return self.value;
    }

    fn Box::set_item(&self, key: u64, value: Number) {
        self.value = value;
    }

    fn main() -> i32 {
        let box: Box = {{ 40 }};
        let two: Number = { 2 };
        box[0] += two;
        return box[0].value;
    }
    """
    assert run(source).returncode == 42


def test_compound_item_assignment_evaluates_its_key_once(run):
    """
    GetItem and SetItem share one evaluated key during a compound write.
    """
    source = """
    @static let calls: i32 = 0;

    fn next() -> u64 {
        calls += 1;
        return 0;
    }

    struct Table: GetItem<u64, i32>, SetItem<u64, i32> {
        value: i32;
    }

    fn Table::get_item(const &self, key: u64) -> i32 {
        return self.value;
    }

    fn Table::set_item(&self, key: u64, value: i32) {
        self.value = value;
    }

    fn main() -> i32 {
        let table: Table = { 40 };
        table[next()] += 2;

        if (calls != 1) { return 1; }
        return table.value;
    }
    """
    assert run(source).returncode == 42


def test_item_interface_claims_check_the_operator_methods(compile_source):
    """
    GetItem and SetItem claims require their corresponding method shapes.
    """
    with pytest.raises(TypeError, match=r"missing the method 'get_item\(u64\) -> i32'"):
        compile_source("""
        struct Broken: GetItem<u64, i32> {}
        fn main() -> i32 { return 0; }
        """)

    with pytest.raises(
            TypeError,
            match=r"missing the method 'set_item\(u64, i32\)'"):
        compile_source("""
        struct Broken: SetItem<u64, i32> {}
        fn main() -> i32 { return 0; }
        """)
