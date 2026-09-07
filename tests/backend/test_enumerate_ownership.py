"""Enumeration owns its copied values and releases iterator state."""

import pytest


RESOURCE = """
@static let clones: i32 = 0;
@static let copy_drops: i32 = 0;
@static let source_drops: i32 = 0;
@static let iterator_drops: i32 = 0;

struct Resource: Destroy, Clone { copied: bool; }
fn Resource::clone(const &self) -> Resource {
    clones += 1;
    let value: Resource = {true};
    return value;
}
fn Resource::destroy(&self) {
    if (self.copied) copy_drops += 1;
    else source_drops += 1;
}

struct Source: Destroy {
    index: u64;
    length: u64;
    value: Resource;
}
fn Source::has_next(&self) -> bool { return self.index < self.length; }
fn Source::next(&self) -> REFERENCE Resource {
    self.index += 1;
    return self.value;
}
fn Source::destroy(&self) {
    drop self.value;
    iterator_drops += 1;
}
fn make(length: u64) -> Source {
    let source: Source = {0, length, {false}};
    return source;
}
"""


@pytest.mark.parametrize("reference", ["&", "const &"])
@pytest.mark.parametrize("length,exit_statement,expected", [
    (0, "", 0),
    (3, "", 3),
    (3, "break;", 1),
    (3, "return;", 1),
    (3, "continue;", 3),
])
def test_enumeration_cleans_up_on_every_exit(run, reference, length,
                                           exit_statement, expected):
    """Empty, completed, interrupted, and continued loops release live state."""
    source = RESOURCE.replace("REFERENCE", reference) + """
    fn exercise() {
        foreach (entry : enumerate(make(LENGTH))) {
            if (source_drops != 0) return;
            EXIT
        }
    }
    fn main() -> i32 {
        exercise();
        if (clones != EXPECTED) return 1;
        if (copy_drops != EXPECTED) return 2;
        if (source_drops != 1) return 3;
        if (iterator_drops != 1) return 4;
        return 0;
    }
    """
    source = (source.replace("LENGTH", str(length))
              .replace("EXIT", exit_statement)
              .replace("EXPECTED", str(expected)))
    assert run(source).returncode == 0


def test_nested_enumeration_releases_each_iterator(run):
    """Nested early exits clean up inner state before continuing the outer loop."""
    assert run(RESOURCE.replace("REFERENCE", "const &") + """
    fn main() -> i32 {
        foreach (outer : enumerate(make(2))) {
            foreach (inner : enumerate(make(3))) { break; }
        }
        if (clones != 4 or copy_drops != 4) return 1;
        if (source_drops != 3 or iterator_drops != 3) return 2;
        return 0;
    }
    """).returncode == 0


def test_manual_next_replaces_and_destroys_the_last_pair(run):
    """Manual iteration destroys the previous pair and its last live pair."""
    assert run(RESOURCE.replace("REFERENCE", "&") + """
    fn exercise() -> i32 {
        let iterator = enumerate(make(2));
        iterator.next();
        if (copy_drops != 0) return 1;
        iterator.next();
        if (copy_drops != 1) return 2;
        return 0;
    }
    fn main() -> i32 {
        let result = exercise();
        if (result != 0) return result;
        if (clones != 2 or copy_drops != 2) return 3;
        if (source_drops != 1 or iterator_drops != 1) return 4;
        return 0;
    }
    """).returncode == 0


@pytest.mark.parametrize("reference", ["&", "const &"])
def test_copied_pair_survives_iterator_destruction(run, reference):
    """Copying a returned pair clones its payload into an independent owner."""
    assert run(RESOURCE.replace("REFERENCE", reference) + """
    fn exercise() -> i32 {
        let iterator = enumerate(make(1));
        let pair = iterator.next();
        drop iterator;
        if (clones != 2 or copy_drops != 1) return 1;
        if (not pair.value.copied) return 2;
        return 0;
    }
    fn main() -> i32 {
        let status = exercise();
        if (status != 0) return status;
        if (copy_drops != 2 or source_drops != 1) return 3;
        return 0;
    }
    """).returncode == 0


def test_enumeration_borrows_collection_elements(run):
    """Copied elements are destroyed without destroying their source array."""
    assert run(RESOURCE.replace("REFERENCE", "const &") + """
    fn main() -> i32 {
        let values: Resource[] = [{false}, {false}];
        foreach (entry : enumerate(values)) {}
        if (source_drops != 0) return 1;
        if (clones != 2 or copy_drops != 2) return 2;
        drop values[0];
        drop values[1];
        return source_drops - 2;
    }
    """).returncode == 0


@pytest.mark.parametrize("reference", ["&", "const &"])
def test_non_cloneable_elements_require_borrowed_iteration(compile_source, reference):
    """Enumeration rejects owners without Clone; direct iteration borrows them."""
    source = """
    struct Resource: Destroy { value: i32; }
    fn Resource::destroy(&self) {}
    fn inspect(values: REFERENCE Resource[]) {
        foreach (entry : ITERABLE) { }
    }
    """.replace("REFERENCE", reference)
    compile_source(source.replace("ITERABLE", "values"))
    with pytest.raises(TypeError, match="implement Clone"):
        compile_source(source.replace("ITERABLE", "enumerate(values)"))


@pytest.mark.parametrize("iterable,value", [
    ("Collection()", "entry"),
    ("enumerate(Collection())", "entry.value"),
])
def test_temporary_collection_outlives_its_iterator(run, iterable, value):
    """A temporary collection remains alive until its loop releases the iterator."""
    source = """
    @static let drops: i32 = 0;
    struct Collection: Destroy, Iterable<i32> { data: i32[1]; }
    fn Collection::init(&self) { self.data[0] = 42; }
    fn Collection::destroy(&self) { drops += 1; }
    fn Collection::iterator(&self) -> ArrayIterator<i32> {
        return ArrayIterator<i32>(self.data);
    }
    fn Collection::const_iterator(const &self) -> ConstArrayIterator<i32> {
        return {self.data, 0};
    }
    fn exercise() -> i32 {
        foreach (entry : ITERABLE) {
            if (drops != 0 or VALUE != 42) return 1;
            return 0;
        }
        return 2;
    }
    fn main() -> i32 {
        let result = exercise();
        if (result != 0) return result;
        return drops - 1;
    }
    """.replace("ITERABLE", iterable).replace("VALUE", value)
    assert run(source).returncode == 0


def test_foreach_clones_a_borrowed_owned_iterator(run):
    """An iterator borrowed through a reference is cloned before consumption."""
    source = RESOURCE.replace("REFERENCE", "&") + """
    @extend Source: Clone;
    fn Source::clone(const &self) -> Source {
        return {0, self.length, self.value.clone()};
    }
    fn inspect(source: const &Source) {
        foreach (value : source) {}
    }
    fn main() -> i32 {
        let source = make(2);
        inspect(source);
        if (source.index != 0 or source_drops != 0) return 1;
        if (clones != 1 or copy_drops != 1 or iterator_drops != 1) return 2;
        drop source;
        if (source_drops != 1 or iterator_drops != 2) return 3;
        return 0;
    }
    """
    assert run(source).returncode == 0
