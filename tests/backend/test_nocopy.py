"""Explicit transfers do not imply automatic resource cleanup."""

import pytest


HANDLE = """
@nocopy
struct Handle { id: i32; }
fn Handle::init(&self, id: i32) { self.id = id; }
fn consume(h: Handle) {}
"""


@pytest.mark.parametrize("body", [
    "let a=Handle(1); let b=a;",
    "let a=Handle(1); consume(a);",
    "let a=Handle(1); let b:Handle={2}; b=a;",
    "let a=Handle(1); let b:const Handle=a;",
    "let a=Handle(1); let b=true ? a : Handle(2);",
    "let a=Handle(1); let b:Tuple<Handle,i32>={a,2};",
    "let a=Handle(1); let b:Handle[]=[a];",
    "let a=Handle(1); let p=&a; let b=*p;",
    "let a:Tuple<Handle,i32>={Handle(1),2}; let (b,c)=a;",
    "let a:Result<Handle,i32>=Ok(Handle(1)); let b=a.value;",
    "let a:Option<Handle>=Handle(1); let b=a;",
    "let a:Option<Handle>=Handle(1); let b=a.value;",
])
def test_implicit_copies_rejected(compile_source, body):
    with pytest.raises(TypeError, match="cannot copy @nocopy"):
        compile_source(HANDLE + "fn main(){" + body + "}")


def test_reference_return_cannot_copy_even_with_clone(compile_source):
    with pytest.raises(TypeError, match="cannot copy @nocopy"):
        compile_source(HANDLE + """
        @extend Handle: Clone;
        fn Handle::clone(const &self)->Handle { return Handle(self.id); }
        fn copy(h:const &Handle)->Handle { return h; }
        fn main(){ let a=Handle(1); let b=copy(a); }
        """)


def test_transfers_and_explicit_clone(run):
    result = run(HANDLE + """
    @extend Handle: Clone;
    fn Handle::clone(const &self)->Handle { return Handle(self.id); }
    fn forward(h:Handle)->Handle { return move h; }
    fn read(h:const &Handle)->i32 { return h.id; }
    fn main()->i32 {
        let a=Handle(7);
        let b=a.clone();
        let c=forward(move a);
        return read(b)+read(c)-14;
    }
    """)
    assert result.returncode == 0


@pytest.mark.parametrize("body", [
    "let a=Handle(1); let b=move a; consume(move a);",
    "let a=Handle(1); while(true){consume(move a);}",
])
def test_move_invalidates_source(compile_source, body):
    with pytest.raises(TypeError, match="moved"):
        compile_source(HANDLE + "fn main(){" + body + "}")


def test_nested_copy_rejected(compile_source):
    with pytest.raises(TypeError, match="cannot copy @nocopy"):
        compile_source(HANDLE + """
        struct Box<T>{value:T;}
        fn main(){let a:Box<Handle>={Handle(1)}; let b=a;}
        """)


def test_inline_array_copy_rejected(compile_source):
    with pytest.raises(TypeError, match="cannot copy @nocopy"):
        compile_source(HANDLE + """
        struct Box{items:@raw<Handle>[1];}
        fn main(){let a:Box; a.items[0]=Handle(1);let b=a;}
        """)


def test_result_transfer_and_try(run):
    result = run(HANDLE + """
    fn make()->Result<Handle,i32>{return Ok(Handle(3));}
    fn main()->i32 {
        let result=make();
        let h=try move result except(e){return e;}
        return h.id-3;
    }
    """)
    assert result.returncode == 0


def test_no_implicit_cleanup(run):
    result = run(HANDLE + """
    @static let drops:i32=0;
    fn Handle::destroy(&self){drops+=1;}
    fn exercise(){let h=Handle(1); consume(move h);}
    fn main()->i32{exercise();return drops;}
    """)
    assert result.returncode == 0


def test_assignment_does_not_call_clone(compile_source):
    with pytest.raises(TypeError, match="cannot copy @nocopy"):
        compile_source(HANDLE + """
        @extend Handle: Clone;
        fn Handle::clone(const &self)->Handle { return Handle(self.id); }
        fn main(){let a=Handle(1);let b=Handle(2);b=a;}
        """)


def test_captured_value_cannot_be_moved_repeatedly(compile_source):
    with pytest.raises(TypeError, match="captured @nocopy"):
        compile_source(HANDLE + """
        fn main(){let a=Handle(1);let f=()=>consume(move a);f();f();}
        """)


@pytest.mark.parametrize('declaration', [
    '@nocopy struct Handle; struct Handle {id:i32;}',
    'struct Handle; @nocopy struct Handle {id:i32;}',
    '@nocopy struct Box<T>; struct Box<T>{id:T;} @type Handle=Box<i32>;',
    'struct Box<T>; @nocopy struct Box<T>{id:T;} @type Handle=Box<i32>;',
    '@nocopy union Handle {id:i32;}',
])
def test_declaration_metadata(compile_source, declaration):
    with pytest.raises(TypeError, match="cannot copy @nocopy"):
        compile_source(declaration +
                       'fn main(){let a:Handle={id=1};let b=a;}')


def test_explicit_move_with_destroy_runs_cleanup_once(run):
    result = run(HANDLE + """
    @static let drops:i32=0;
    @extend Handle: Destroy;
    fn Handle::destroy(&self){drops+=1;}
    fn exercise(){let a=Handle(1); let b=move a;consume(move b);}
    fn main()->i32 {exercise();return drops-1;}
    """)
    assert result.returncode == 0


def test_copyable_fields_and_pointers_remain_copyable(run):
    result = run(HANDLE + """
    struct View{pointer:Handle*;}
    fn main()->i32{
        let a=Handle(7); let b=a.id;
        let p:View={&a};let q=p;
        return q.pointer->id-b;
    }
    """)
    assert result.returncode == 0


@pytest.mark.parametrize('extra,body', [
    ('struct Box{handle:Handle=shared;}', 'let a:Box;'),
    ('fn use(h:Handle=shared){}', 'use();'),
])
def test_defaults_cannot_copy_global(compile_source, extra, body):
    with pytest.raises(TypeError, match="cannot copy @nocopy"):
        compile_source(HANDLE + '@static let shared:Handle={1};' + extra
                       + 'fn main(){' + body + '}')


def test_option_and_tuple_moves(run):
    result = run(HANDLE + """
    fn main()->i32 {
        let option:Option<Handle>=Handle(7);
        if(option){
            let h:Handle=move option;
            let pair:Tuple<Handle,i32>={move h,2};
            let (a,b)=move pair;
            return a.id+b-9;
        }
        return 1;
    }
    """)
    assert result.returncode == 0


def test_move_then_reinitialize(run):
    result = run(HANDLE + """
    fn main()->i32 {
        let a=Handle(1);consume(move a);
        a=Handle(2);
        return a.id-2;
    }
    """)
    assert result.returncode == 0


def test_moving_a_reference_rejected(compile_source):
    with pytest.raises(TypeError, match="cannot move"):
        compile_source(HANDLE + """
        fn take(h:&Handle)->Handle{return move h;}
        fn main(){let a=Handle(1);let b=take(a);}
        """)


def test_option_move_requires_present_value(compile_source):
    with pytest.raises(TypeError, match="check that it is present"):
        compile_source(HANDLE + """
        fn take(h:Option<Handle>)->Handle{return move h;}
        fn main(){let a:Option<Handle>=None;let b=take(move a);}
        """)
