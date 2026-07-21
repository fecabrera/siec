"""Feature tests for the '->' pointer member operator."""

import pytest


def test_arrow_reads_a_field_through_a_pointer(run):
    """
    'p->field' reads the field of the struct the pointer points at.
    """
    source = """
    struct Point {
        x: i32;
        y: i32;
    }

    fn main() -> i32 {
        let pt: Point = {40, 2};
        let p: Point* = &pt;
        return p->x + p->y;
    }
    """
    assert run(source).returncode == 42


def test_arrow_writes_a_field_through_a_pointer(run):
    """
    'p->field = v' writes the field inside the pointed-at struct.
    """
    source = """
    struct Point {
        x: i32;
        y: i32;
    }

    fn main() -> i32 {
        let pt: Point = {1, 2};
        let p: Point* = &pt;
        p->x = 40;
        p->y += 0;
        return pt.x + pt.y;
    }
    """
    assert run(source).returncode == 42


def test_arrow_chains_through_linked_nodes(run):
    """
    'a->next->value' dereferences each link of a pointer chain.
    """
    source = """
    struct Node {
        value: i32;
        next: Node*;
    }

    fn main() -> i32 {
        let second: Node = {42, null};
        let first: Node = {1, &second};
        let p: Node* = &first;
        return p->next->value;
    }
    """
    assert run(source).returncode == 42


def test_arrow_and_dot_mix_along_a_chain(run):
    """
    'q.head->value' follows a struct's pointer field, '.'-then-'->'.
    """
    source = """
    struct Node {
        value: i32;
    }

    struct List {
        head: Node*;
    }

    fn main() -> i32 {
        let n: Node = {42};
        let l: List = {&n};
        return l.head->value;
    }
    """
    assert run(source).returncode == 42


def test_arrow_calls_a_method_on_the_pointed_at_struct(run):
    """
    'p->method()' calls the method with the dereferenced struct as receiver.
    """
    source = """
    struct Counter {
        count: i32;
    }

    fn Counter::bump(&self) {
        self.count += 1;
    }

    fn main() -> i32 {
        let c: Counter = {41};
        let p: Counter* = &c;
        p->bump();
        return c.count;
    }
    """
    assert run(source).returncode == 42


def test_arrow_on_a_non_pointer_is_an_error(compile_source):
    """
    '->' demands a pointer base; a struct value takes '.' instead.
    """
    with pytest.raises(TypeError, match="cannot index"):
        compile_source("""
        struct Point {
            x: i32;
        }

        fn main() -> i32 {
            let pt: Point = {1};
            return pt->x;
        }
        """)
