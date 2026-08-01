"""Runtime ownership tests for the core List and Map slot storage."""

import subprocess
from pathlib import Path

from tests.cli.test_cli import run_cli


ROOT = Path(__file__).parents[2]
CORE_INCLUDES = (
    ROOT / "packages/libc/src",
    ROOT / "packages/posix/src",
    ROOT / "packages/core/src",
)


def run_core(monkeypatch, tmp_path, source: str, *arguments: str):
    """Compile and run one program against the workspace core sources."""
    path = tmp_path / "main.sie"
    executable = tmp_path / "main"
    path.write_text(source)

    args = [path]
    for include in CORE_INCLUDES:
        args.extend(("-I", include))
    args.extend(("-o", executable))

    assert run_cli(monkeypatch, *args) == 0
    return subprocess.run(
        [str(executable), *arguments], capture_output=True, text=True)


RESOURCE = r"""
import { String } from std.collections;
import { Formattable } from std.format;

@static let drops: i32 = 0;
@static let dropped_ids: i32 = 0;
@static let clones: i32 = 0;

struct Resource: Destroy, Clone, Formattable { id: i32; }
fn Resource::init(&self, id: i32) { self.id = id; }
fn Resource::destroy(&self) {
    drops += 1;
    dropped_ids += self.id;
}
fn Resource::clone(const &self) -> Resource {
    clones += 1;
    return Resource(self.id);
}
fn Resource::format(const &self, modifiers: const &char[]) -> String {
    return String();
}
"""


def test_list_slots_own_growth_replacement_pop_and_reset(monkeypatch, tmp_path):
    """List transfers each live slot exactly once across every operation."""
    result = run_core(monkeypatch, tmp_path, RESOURCE + r"""
    import { List } from std.collections;

    fn exercise() -> i32 {
        let list = List<Resource>();
        list.push(Resource(1));
        list.push(Resource(2));
        list[0] = Resource(3);
        let popped = list.pop();
        list.push(Resource(4));

        let source = Resource(5);
        list.push_from(source);
        list.reset();

        {
            let nested = List<List<Resource>>();
            let inner = List<Resource>();
            inner.push(Resource(6));
            nested.push(move inner);
        }
        return popped.id;
    }

    fn main() -> i32 {
        if (exercise() != 2) return 1;
        if (clones != 1 or drops != 7 or dropped_ids != 26) return 2;
        return 42;
    }
    """)
    assert result.returncode == 42


def test_map_slots_own_insert_replace_remove_grow_and_destroy(
        monkeypatch, tmp_path):
    """Map owns only occupied key/value slots, including after rehashing."""
    result = run_core(monkeypatch, tmp_path, RESOURCE + r"""
    import { Map } from std.collections;

    fn exercise() {
        let map = Map<char[], Resource>();
        map.set("a", Resource(1));
        map.set("a", Resource(2));
        map.set("b", Resource(3));
        map.remove("b");
        map.set("c", Resource(4));
        map.set("d", Resource(5));
        map.set("e", Resource(6));
        map.set("f", Resource(7));

        let out = Resource(8);
        map.get("a", out);
    }

    fn main() -> i32 {
        exercise();
        if (clones != 1 or drops != 9 or dropped_ids != 38) return 1;
        return 42;
    }
    """)
    assert result.returncode == 42


def test_stack_slots_transfer_growth_pop_peek_and_destroy(
        monkeypatch, tmp_path):
    """Stack owns its live slot prefix and exposes only a const top borrow."""
    result = run_core(monkeypatch, tmp_path, RESOURCE + r"""
    import { Stack } from std.collections;

    fn exercise() -> i32 {
        let stack = Stack<Resource>(1);
        stack.push(Resource(1));
        stack.push(Resource(2));
        stack.push(Resource(3));

        let source = Resource(5);
        stack.push_from(source);

        if (stack.peek().id != 5) return 1;
        stack.pop();
        let popped = stack.pop();
        if (popped.id != 3 or stack.peek().id != 2) return 2;

        {
            let outer = Stack<Stack<Resource>>();
            let inner = Stack<Resource>();
            inner.push(Resource(4));
            outer.push(move inner);
        }
        return 42;
    }

    fn main() -> i32 {
        if (exercise() != 42) return 1;
        if (clones != 1 or drops != 6 or dropped_ids != 20) return 2;
        return 42;
    }
    """)
    assert result.returncode == 42


def test_queue_slots_transfer_pop_peek_iteration_and_destroy(
        monkeypatch, tmp_path):
    """Queue nodes own one slot until pop or queue destruction ends it."""
    result = run_core(monkeypatch, tmp_path, RESOURCE + r"""
    import { Queue } from std.collections;

    fn exercise() -> i32 {
        let queue = Queue<Resource>();
        queue.push(Resource(1));
        queue.push(Resource(2));
        queue.push(Resource(3));

        let source = Resource(5);
        queue.push_from(source);

        if (queue.peek().id != 1) return 1;

        let iterator = queue.iterator();
        if (iterator.next().id != 1 or iterator.next().id != 2) return 2;

        let const_iterator = queue.const_iterator();
        if (const_iterator.next().id != 1) return 3;

        let popped = queue.pop();
        if (popped.id != 1 or queue.peek().id != 2) return 4;

        {
            let outer = Queue<Queue<Resource>>();
            let inner = Queue<Resource>();
            inner.push(Resource(4));
            outer.push(move inner);
        }
        return 42;
    }

    fn main() -> i32 {
        if (exercise() != 42) return 1;
        if (clones != 1 or drops != 6 or dropped_ids != 20) return 2;
        return 42;
    }
    """)
    assert result.returncode == 42


def test_empty_container_access_panics(monkeypatch, tmp_path):
    """Element-requiring core APIs diagnose empty or exhausted storage."""
    source = r"""
    import { List, Queue, Stack } from std.collections;
    import { atoi } from stdlib;

    fn main(argc: i32, argv: char**) -> i32 {
        let operation = atoi(argv[1]);

        if (operation == 0) {
            let list = List<i32>();
            list.pop();
        }
        if (operation == 1) {
            let list = List<i32>();
            let value = list[0];
        }
        if (operation == 2) {
            let list = List<i32>();
            list[0] = 42;
        }
        if (operation == 3) {
            let stack = Stack<i32>();
            stack.pop();
        }
        if (operation == 4) {
            let stack = Stack<i32>();
            stack.peek();
        }
        if (operation == 5) {
            let queue = Queue<i32>();
            queue.pop();
        }
        if (operation == 6) {
            let queue = Queue<i32>();
            queue.peek();
        }
        if (operation == 7) {
            let queue = Queue<i32>();
            let iterator = queue.iterator();
            iterator.next();
        }

        let queue = Queue<i32>();
        let iterator = queue.const_iterator();
        iterator.next();
        return 0;
    }
    """
    expected = (
        "cannot pop an empty List",
        "List index is out of bounds",
        "List index is out of bounds",
        "cannot pop an empty Stack",
        "cannot peek an empty Stack",
        "cannot pop an empty Queue",
        "cannot peek an empty Queue",
        "cannot advance an exhausted QueueIterator",
        "cannot advance an exhausted ConstQueueIterator",
    )

    executable = tmp_path / "main"
    for operation, message in enumerate(expected):
        if operation == 0:
            result = run_core(monkeypatch, tmp_path, source, str(operation))
        else:
            result = subprocess.run(
                [str(executable), str(operation)],
                capture_output=True,
                text=True,
            )
        assert result.returncode != 0
        assert message in result.stderr


def test_const_queue_iterator_advances_without_mutating_values(
        monkeypatch, tmp_path):
    """A const queue iterator advances its cursor and borrows each value."""
    result = run_core(monkeypatch, tmp_path, r"""
    import { Queue } from std.collections;

    fn main() -> i32 {
        let queue = Queue<i32>();
        queue.push(20);
        queue.push(22);

        let iterator = queue.const_iterator();
        if (iterator.next() != 20) return 1;
        if (iterator.next() != 22) return 2;
        if (iterator.has_next()) return 3;
        return 42;
    }
    """)
    assert result.returncode == 42
