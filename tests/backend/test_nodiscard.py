"""Required results must survive expressions and indirect calls."""

import pytest


DECL = '@nodiscard fn f() -> i32 { return 1; }\n'


@pytest.mark.parametrize('statement', ['f();', 'f() + 1;', 'f() != 0;',
                                      '{ f(); }', 'if (true) { f(); }'])
def test_discard(compile_source, statement):
    """Reject discarded results at different statement depths."""
    with pytest.raises(TypeError, match='is not used'):
        compile_source(DECL + 'fn main() { ' + statement + ' }')


@pytest.mark.parametrize('value', ['f()', 'f() + 1', 'true ? f() : 0'])
def test_forward(compile_source, value):
    """Require a declaration on each forwarding function."""
    with pytest.raises(TypeError, match='must declare @nodiscard'):
        compile_source(DECL + 'fn g() -> i32 { return ' + value + '; }')
    compile_source(DECL + '@nodiscard fn g() -> i32 { return ' + value + '; }')


@pytest.mark.parametrize('body', [
    'let v = f();', 'let v = f() + 1;', 'if (f() != 0) {}',
    'let v = f(); return v;', 'return true ? 0 : 1;',
])
def test_used(compile_source, body):
    """Storage ends the obligation; conditions use their results."""
    compile_source(DECL + 'fn g() -> i32 { ' + body + ' return 0; }')


def test_alias(compile_source):
    """Storing the function itself preserves its call requirement."""
    with pytest.raises(TypeError, match='is not used'):
        compile_source(DECL + 'fn main() { let cb = f; cb(); }')


def test_unannotated(compile_source):
    """Ordinary calls can still discard their results."""
    compile_source('fn f() -> i32 { return 1; } fn main() { f(); f()+1; }')


def test_requires_value(compile_source):
    """A function with no result cannot require its use."""
    with pytest.raises(TypeError, match='requires a function that returns a value'):
        compile_source('@nodiscard fn f() {}')


@pytest.mark.parametrize('source', [
    'fn invoke(cb: fn()->i32) { cb(); } fn main() { invoke(f); }',
    'fn invoke(cb: fn()->i32)->i32 { return cb(); } fn main() { let x=invoke(f); }',
    'fn choose()->fn()->i32 { return f; } fn main() { let cb=choose(); cb(); }',
    'fn main() { let cb=f; let alias=cb; alias(); }',
    'fn invoke(cb: fn()->i32 = f) { cb(); } fn main() { invoke(); }',
    '@static let cb: fn()->i32; fn main() { cb=f; cb(); }',
    'fn main() { fn cb()->i32 { return f(); } let x=cb(); }',
])
def test_indirect_contract(compile_source, source):
    """Preserve contracts through aliases, callbacks, and returned functions."""
    with pytest.raises(TypeError, match='is not used|must declare @nodiscard'):
        compile_source(DECL + source)


@pytest.mark.parametrize('source', [
    '@macro call() = f(); fn main() { call(); }',
    '@macro value = f(); fn main() { value; }',
    '@macro call() { emit f(); } fn main() { call(); }',
    'fn main() { defer f(); }',
    'fn main() { while (false) { f(); } }',
    '@nodiscard fn id<T>(v:T)->T { return v; } fn main() { id(1); }',
    'struct S { x:i32; @nodiscard fn get(const &self)->i32 { return self.x; } } '
    'fn main() { let s:S={1}; s.get(); }',
    '@nodiscard fn forward()->i32 { return f(); } fn main() { forward(); }',
    '@nodiscard fn external()->i32; fn external()->i32 { return 1; } fn main() { external(); }',
    '@extern @nodiscard fn external()->i32; fn main() { external(); }',
])
def test_other_call_forms(compile_source, source):
    """Apply the rule to resolved declarations and expanded expressions."""
    with pytest.raises(TypeError, match='is not used'):
        compile_source(DECL + source)


def test_execution(run):
    """Valid result uses keep their runtime behavior."""
    result = run(DECL + '''
        @nodiscard fn adjusted()->i32 { return f()+1; }
        fn main()->i32 {
            let cb=f;
            let value=cb();
            if (adjusted()!=2) { return 9; }
            return value-1;
        }
    ''')
    assert result.returncode == 0


@pytest.mark.parametrize('body', [
    'let x = try fail() except (e) { f(); emit 0; }',
    'try fail() except (e) { emit f(); }',
    'return try fail() except (e) { emit f(); }',
])
def test_try_body(compile_source, body):
    """Check statements and emitted results in an error handler."""
    with pytest.raises(TypeError, match='is not used|must declare @nodiscard'):
        compile_source(DECL + '''
            fn fail()->Result<i32,u8> { return Error(1); }
            fn g()->i32 { ''' + body + ' return 0; }')


def test_try_uses_result(compile_source):
    """Checking a Result for an error satisfies its required use."""
    compile_source('''
        @nodiscard fn f()->Result<u8> { return Ok(); }
        fn main() { try f() except (e) { let error=e; } }
    ''')


def test_argument_uses_result(compile_source):
    """Passing a result to a function counts as use."""
    compile_source(DECL + '''
        fn consume(x:i32)->i32 { return x; }
        fn main() { consume(f()); }
    ''')


def test_overload_contract(compile_source):
    """Only the selected overload contributes its annotation."""
    compile_source(DECL + '''
        fn f(x:i32)->i32 { return x; }
        fn main() { f(1); }
    ''')


def test_operator_contract(compile_source):
    """Operator syntax keeps the selected method's requirement."""
    with pytest.raises(TypeError, match='is not used'):
        compile_source('''
            struct S : Add<i32,S> { x:i32; }
            @nodiscard fn S::add(const &self, other:S)->i32 {
                return self.x+other.x;
            }
            fn main() { let a:S={1}; let b:S={2}; a+b; }
        ''')


@pytest.mark.parametrize('source', [
    '@nodiscard fn id<T>(x:T)->T { return x; } '
    'fn main() { let cb=id<i32>; cb(1); }',
    '@static @nodiscard fn f()->i32 { return 1; } '
    'fn main() { let cb=f; cb(); }',
    '@nodiscard fn f()->i32 { return 1; } '
    'fn main() { let cb=() -> i32 => { let x=f(); return x; }; cb(); }',
])
def test_reference_forms(compile_source, source):
    """Check generic and static references; stored closure results are ordinary."""
    if 'let x=f()' in source:
        compile_source(source)
    else:
        with pytest.raises(TypeError, match='is not used'):
            compile_source(source)


def test_static_reference_file_view():
    """Resolve inferred static references under their enclosing file."""
    from siec.codegen import codegen
    from siec.lexer import lex
    from siec.parser import parse

    program = parse(lex('''
        @static @nodiscard fn f()->i32 { return 1; }
        fn main() { let cb=f; cb(); }
    '''))
    for fn in program.functions:
        fn.file = 'source.sie'
    with pytest.raises(TypeError, match='is not used'):
        codegen(program, 'm')
