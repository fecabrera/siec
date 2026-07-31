"""Feature tests for char literals."""


def test_char_literals_compare_with_chars(run):
    """
    A char literal is a 'char': it compares with string elements directly.
    """
    source = """
    fn main() -> i32 {
        let s: char* = "cab";
        if (s[0] == 'c' and s[1] != 'b' and '\\x41' == 'A') {
            return 42;
        }
        return 0;
    }
    """
    assert run(source).returncode == 42


def test_char_literal_infers_char(run):
    """
    'let c = 'a';' infers char, usable where chars are.
    """
    source = """
    fn shout(c: char) -> bool {
        return c == '!';
    }

    fn main() -> i32 {
        let c = '!';
        return shout(c) ? 42 : 0;
    }
    """
    assert run(source).returncode == 42


def test_chars_in_case_arms(run):
    """
    Char literals make natural 'when' values.
    """
    source = """
    fn kind(c: char) -> i32 {
        case (c) {
            when 'a', 'e', 'i', 'o', 'u': return 1;
            when ' ', '\\t', '\\n':       return 2;
            else:                         return 0;
        }
    }

    fn main() -> i32 {
        return kind('e') * 100 + kind(' ') * 10 + kind('z'); // 120
    }
    """
    assert run(source).returncode == 120


def test_chars_in_constants_and_globals(run):
    """
    '@const' and '@static let' both take char literal values.
    """
    source = """
    @const TERMINATOR = '\\0';

    @static let separator: char = ',';

    fn main() -> i32 {
        if ("x"[1] == TERMINATOR and separator == ',') {
            return 42;
        }
        return 0;
    }
    """
    assert run(source).returncode == 42


def test_integer_literals_fill_char_and_bool_targets(run):
    """
    A bare integer literal adopts a 'char' or 'bool' target like the
    small integer it emits as: declarations, element assignments, and
    call arguments alike.
    """
    source = """
    fn take(c: char) -> i32 {
        return c as i32;
    }

    fn main() -> i32 {
        let c: char = 40;
        let flag: bool = 1;
        let buf: char[4];
        buf[0] = 0;
        buf[1] = 2;
        if (flag) {
            return c as i32 + buf[1] as i32 + take(0);
        }
        return 0;
    }
    """
    assert run(source).returncode == 42
