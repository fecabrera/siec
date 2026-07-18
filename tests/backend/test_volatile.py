"""Feature tests for '@volatile' structs."""

from siec.backend import prepare_module

REG = """
@volatile struct Reg {
    status: u32;
    data: u32;
}
"""


def test_volatile_marks_every_access(compile_source):
    """
    Reads and writes of a volatile struct — whole values, fields, and
    through pointers — all carry the volatile flag.
    """
    module = str(compile_source(REG + """
    fn main() -> i32 {
        let r: Reg = { 1, 2 };   // volatile init store
        r.status = 3;            // volatile field store
        let copy = r;            // volatile whole load + store
        let p: Reg* = &r;
        p[0].data = 4;           // volatile store through a pointer
        return (copy.status + p[0].data) as i32;
    }
    """))
    assert module.count("store volatile") >= 4
    assert module.count("load volatile") >= 2


def test_plain_structs_stay_non_volatile(compile_source):
    """
    Only decorated structs pay the volatile cost.
    """
    module = str(compile_source("""
    struct Plain { x: i32; }
    fn main() -> i32 {
        let p: Plain = { 1 };
        p.x = 2;
        return p.x;
    }
    """))
    assert "volatile" not in module


def test_volatile_stores_survive_optimization(compile_source):
    """
    The optimizer must not elide dead stores to a volatile struct.
    """
    module = compile_source(REG + """
    fn main() -> i32 {
        let r: Reg = { 1, 1 };
        r.data = 2;   // dead, but volatile: must remain
        r.data = 3;
        return r.data as i32;
    }
    """)
    optimized = str(prepare_module(module, opt=2)[1])
    assert optimized.count("store volatile i32") >= 2


def test_volatile_struct_behaves_normally(run):
    """
    Volatility changes emission, never results.
    """
    source = REG + """
    fn main() -> i32 {
        let r: Reg = { 2, 40 };
        r.status = r.status + r.data;
        return r.status as i32;
    }
    """
    assert run(source).returncode == 42
