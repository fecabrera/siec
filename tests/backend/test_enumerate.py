"""Feature tests for the builtin 'enumerate()' function."""

import pytest


def test_enumerate_pairs_indices_with_values(run, compile_source):
    """
    'enumerate(x)' wraps mutable and const Iterables or iterators, its
    'next()' referencing a pair with the element's contract preserved.
    """
    source = """
    struct List<T>: Iterable<T> {
        data: T*;
        length: u64;
    }

    fn List<T>::iterator(&self) -> ArrayIterator<T> {
        return ArrayIterator<T>({self.data!, self.length});
    }
    fn List<T>::const_iterator(const &self) -> ConstArrayIterator<T> {
        let it: ConstArrayIterator<T> = { {self.data!, self.length}, 0 };
        return it;
    }

    fn const_pair_score(
        pairs: ConstIterator<ConstEnumerated<char[]>>
    ) -> u64 {
        let score: u64 = 0;
        foreach (e : pairs) {
            score += e.value.length;
        }
        return score;
    }

    fn const_score(values: const &char[][]) -> u64 {
        let score: u64 = 0;
        foreach (e : enumerate(values)) {
            score += e.index + e.value.length;
        }
        let it = values.const_iterator();
        score += const_pair_score(enumerate(it));
        return score;
    }

    fn main() -> i32 {
        let nums: i32[] = [10, 12, 20];

        let weighted: u64 = 0;
        foreach (e : enumerate(nums)) {
            weighted += e.index * (e.value as u64);
        }
        if (weighted != 52) { return 1; }    // 0*10 + 1*12 + 2*20

        let l: List<i32> = { nums.data, nums.length };
        let last: u64 = 99;
        foreach (e : enumerate(l)) {
            last = e.index;
        }
        if (last != 2) { return 2; }

        let it = nums.iterator();            // an iterator, mid-stream
        it.next();
        foreach (e : enumerate(it)) {
            if (e.index == 0 and e.value != 12) { return 3; }
        }

        let en = enumerate(nums);            // a plain iterator value
        let first = en.next();
        if (first.index != 0 or first.value != 10) { return 4; }

        let ro: const i32[] = nums;          // const arrays enumerate too
        let count: u64 = 0;
        foreach (e : enumerate(ro)) {
            count += 1;
        }
        if (count != 3) { return 5; }

        let words: char[][] = ["a", "bc"];
        return (const_score(words) as i32) - 7;
    }
    """
    assert run(source).returncode == 0

    with pytest.raises(TypeError, match="cannot assign to const field 'value'"):
        compile_source("""
        fn tamper(values: const &i32[]) {
            foreach (e : enumerate(values)) {
                e.value = 0;
            }
        }
        fn main() -> i32 { return 0; }
        """)


def test_enumerate_carried_foreign_types(tmp_path, monkeypatch):
    """
    Enumerating a foreign generic field works without the caller ever
    importing the generic struct behind it: the instantiation's type
    arguments are carried names, gated by no file's view.
    """
    from tests.cli.test_cli import run_cli

    coll = tmp_path / "coll"
    coll.mkdir()
    (coll / "list.sie").write_text("""
        struct List<T>: Iterable<T> { data: T*; length: u64; }
        fn List<T>::iterator(&self) -> ArrayIterator<T> {
            return ArrayIterator<T>({self.data!, self.length});
        }
        fn List<T>::const_iterator(const &self) -> ConstArrayIterator<T> {
            let it: ConstArrayIterator<T> = { {self.data!, self.length}, 0 };
            return it;
        }
    """)

    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "info.sie").write_text("""
        import { List } from coll.list;

        struct Info { include: List<List<u8>>; }
        fn Info::init(&self) {
            let empty: List<u8>[] = [];
            self.include.data = empty.data;
            self.include.length = 0;
        }
    """)

    src = tmp_path / "main.sie"
    src.write_text("""
        import { Info } from pack.info;

        fn main() -> i32 {
            let info = Info();

            let count: u64 = 0;
            foreach (el : enumerate(info.include)) {
                count += 1;
            }
            return count as i32;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 0


def test_enumerate_rejects_non_iterables(compile_source):
    """
    A value that cannot iterate cannot enumerate.
    """
    with pytest.raises(TypeError, match="cannot enumerate a 'i32' value"):
        compile_source("""
        fn main() -> i32 {
            let n = 5;
            foreach (e : enumerate(n)) { }
            return 0;
        }
        """)


def test_a_user_enumerate_wins(run):
    """
    A declared function named 'enumerate' takes precedence.
    """
    source = """
    fn enumerate(a: i32, b: i32) -> i32 { return a + b; }
    fn main() -> i32 { return enumerate(40, 2) - 42; }
    """
    assert run(source).returncode == 0
