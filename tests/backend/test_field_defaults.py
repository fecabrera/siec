"""Feature tests for struct field defaults: 'name: type = value;'."""

import pytest


def test_bare_declarations_start_from_the_defaults(run):
    """
    'let s: S;' on a struct with defaults holds them, undefaulted fields
    zeroed; nested struct defaults cascade.
    """
    source = """
    struct List<T> {
        data: T* = null;
        length: u64;
        capacity: u64 = 8;
    }

    struct Config {
        verbose: bool = true;
        level: i32 = 40;
    }

    struct App {
        cfg: Config;
        id: i32;
    }

    fn main() -> i32 {
        let l: List<i32>;
        let a: App;

        if (l.data != null or l.length != 0 or l.capacity != 8) { return 1; }
        if (not a.cfg.verbose or a.id != 0) { return 2; }

        return a.cfg.level + 2;
    }
    """
    assert run(source).returncode == 42


def test_named_literals_default_the_unfilled_fields(run):
    """
    A named literal fills what it names; the rest take their defaults
    instead of zero.
    """
    source = """
    struct Config {
        verbose: bool = true;
        level: i32 = 40;
        tag: char* = "sie";
    }

    fn main() -> i32 {
        let c: Config = { level = 2 };

        if (not c.verbose or c.tag[0] != "s"[0]) { return 1; }
        return c.level + 40;
    }
    """
    assert run(source).returncode == 42


def test_union_fields_take_no_default(compile_source):
    """
    A union's fields share one storage; no single member's default could
    fill it.
    """
    with pytest.raises(TypeError, match="a union field cannot have a default"):
        compile_source("""
        union U { i: i64 = 5; f: f64; }
        fn main() -> i32 { return 0; }
        """)
