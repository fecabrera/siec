"""Feature tests for 'case ... when' statements."""


def test_case_runs_the_matching_arm(run):
    """
    The first arm whose value equals the subject runs; the rest don't.
    """
    source = """
    fn pick(n: i32) -> i32 {
        case (n) {
            when 1: return 10;
            when 2: return 20;
            else:   return 0;
        }
    }

    fn main() -> i32 {
        return pick(1) + pick(2) + pick(9); // 10 + 20 + 0
    }
    """
    assert run(source).returncode == 30


def test_arms_do_not_fall_through(run):
    """
    After an arm runs, control jumps past the case.
    """
    source = """
    fn main() -> i32 {
        let r: i32 = 0;
        case (1) {
            when 1: r += 40;
            when 2: r += 100;   // must not also run
        }
        return r + 2;
    }
    """
    assert run(source).returncode == 42


def test_subject_evaluates_once(run):
    """
    The subject expression runs a single time, however many arms test it.
    """
    source = """
    @static let evals: i32;

    fn subject() -> i32 {
        evals += 1;
        return 3;
    }

    fn main() -> i32 {
        case (subject()) {
            when 1: return 1;
            when 2: return 2;
            when 3: return 40 + evals * 2; // 40 + 2
        }
        return 0;
    }
    """
    assert run(source).returncode == 42


def test_case_matches_enum_members(run):
    """
    'when' values are expressions: enum members compare by value.
    """
    source = """
    enum Op { ADD, SUB }

    fn apply(op: Op, a: i32, b: i32) -> i32 {
        case (op) {
            when Op::ADD: return a + b;
            when Op::SUB: return a - b;
        }
        return 0;
    }

    fn main() -> i32 {
        return apply(Op::ADD, 40, 2) + apply(Op::SUB, 2, 2); // 42 + 0
    }
    """
    assert run(source).returncode == 42


def test_unmatched_without_else_does_nothing(run):
    """
    With no else, an unmatched subject just moves past the case.
    """
    source = """
    fn main() -> i32 {
        let r: i32 = 42;
        case (9) {
            when 1: r = 0;
        }
        return r;
    }
    """
    assert run(source).returncode == 42


def test_arms_have_their_own_scope(run):
    """
    A variable declared inside an arm ends with it.
    """
    source = """
    fn main() -> i32 {
        let r: i32 = 0;
        case (1) {
            when 1:
                let local: i32 = 42;
                r = local;
        }
        return r;
    }
    """
    assert run(source).returncode == 42


def test_every_arm_may_return(run):
    """
    A case where all paths return leaves no fall-through edge.
    """
    source = """
    fn choose(n: i32) -> i32 {
        case (n) {
            when 1: return 41;
            else:   return 1;
        }
    }

    fn main() -> i32 {
        return choose(1) + choose(5);
    }
    """
    assert run(source).returncode == 42


def test_if_else_nests_inside_an_arm(run):
    """
    An arm's if keeps its own else; 'else:' still closes the case.
    """
    source = """
    fn main() -> i32 {
        let r: i32 = 0;
        case (2) {
            when 2:
                if (r == 0) {
                    r = 40;
                } else {
                    r = 1;
                }
                r += 2;
            else:
                r = 9;
        }
        return r;
    }
    """
    assert run(source).returncode == 42
