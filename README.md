# sielang

Sie is a a modern C-flavored language with a strong typing system and minimal syntax. The main goal of this project is to simplify the coding experience for programmers while providing full low-level control.

## Hello world

```
import std.io;

fn main() -> i32 {
    std.io.println("hello world");
    return 0;
}
```

## The compiler

Programs are compiled through `siec`, which takes one or more source files and links them into an executable:

```
siec main.sie -o main
```

Precompiled object files may be given alongside the sources; they skip compilation and link into the executable (and `--run` resolves their symbols too):

```
siec main.sie file1.o file2.o -o main
```

- `-o <path>` names the output executable, `a.out` by default.
- `-c` compiles to an object file without linking, named after the source (`main.sie` → `main.o`) unless `-o` says otherwise.
- `-I <dir>` adds a directory to the include search path. The `lib/` directory next to each source file is always searched.
- `-l <lib>` links against a library, passed through to the linker: `-l m` links the C math library.
- `-L <dir>` adds a directory to the library search path.
- `--emit-llvm` prints the LLVM IR and exits, without building.
- `--emit-asm` prints the host target's native assembly and exits, without building.
- `--run` JIT-compiles and runs the program in place of building it, exiting with the program's own exit code. Anything after the flag is passed along as its arguments:

```
siec main.sie --run arg1 arg2
```

## The language

### Imports

Modules are pulled from the code being compiled through the `import` keyword followed by the module's dotted path. Their members are accessed through their qualified name:

```
import std.io;

fn main() -> i32 {
    std.io.println("hello world");
    return 0;
}
```

Optionally, you can pick specific members of a module through `{}` and `from`, which brings them into scope unqualified:

```
import { f } from module.submodule;
```

Both members and modules can be aliased through `as`:

```
import { f as g } from module.submodule;
import module.submodule as sub;
```

#### Include

`@include("path")` pulls a specific `.sie` file directly into the current file, searching the include path:

```
@include("libc/stdio")
```

Unlike `import`, an include copies the file's declarations as if they were written in place, without any namespacing.

### Variables

Variables are values in memory that can be declared through the `let` keyword:

```
let v: T;   // a scalar
let v: T*;  // a pointer
let s: T[]; // an array
```

They can be initialized by adding `= <expr>` after their declaration.

```
let v: T = <expr>; // v is a scalar that holds the value <expr>
```

Their value can be assigned at runtime through the operator `=`:

```
v = <expr>;
```

### Constants

Constants are compile-time constant expressions declared through `@const`. Unlike a `let` variable, a constant has no storage of its own: it's substituted with its value at compile time, similar to a type-safe version of C's `#define`. They must be initialized and cannot be reassigned. The type annotation is optional, inferred from the value when omitted:

```
@const name: T = <value>;
@const name = <value>; // type inferred
```

### Arithmetic

Numeric values can be combined through the usual arithmetic operators:

- Addition: `+`
- Subtraction: `-`
- Multiplication: `*`
- Division: `/`
- Remainder: `%`
- Power: `**`

```
let a: i32 = 10;
let b: i32 = a + 5 * 2; // b holds the value 20
```

Multiplication, division and remainder bind tighter than addition and subtraction. Subexpressions can be grouped through parentheses to override that order:

```
let c: i32 = (a + 5) * 2; // c holds the value 30
```

Power binds tighter than multiplication, division and remainder:

```
let f: i32 = 2 * 3 ** 2; // f holds the value 18
```

Values can be negated through the unary operator `-`, which binds tighter than any binary operator:

```
let d: i32 = -c;       // d holds the value -30
let e: i32 = -(a + 5); // e holds the value -15
```

### Bitwise

Integer values can be combined through the usual bitwise operators:

- Left shift: `<<`
- Right shift: `>>`
- AND: `&`
- OR: `|`
- XOR: `^`
- NOT: `~`

```
let a: i32 = 6;
let b: i32 = a & 3;  // b holds the value 2
let c: i32 = 1 << 3; // c holds the value 8
```

### Logical

Boolean values can be combined through the usual logical operators:

- AND: `and`
- OR: `or`
- NOT: `not`

```
let a: bool = true;
let b: bool = a and false; // b holds the value false
let c: bool = not a;       // c holds the value false
```

### Compound assignment

Arithmetic and bitwise operators can be combined with `=` into a compound assignment, updating a variable in place with the result of applying the operator to its current value:

- Arithmetic: `+=`, `-=`, `*=`, `/=`, `%=`, `**=`
- Bitwise: `<<=`, `>>=`, `&=`, `|=`, `^=`

```
let a: i32 = 10;
a += 5; // a holds the value 15, equivalent to a = a + 5
```

### Truthyness

Values other than `bool` can still be used wherever a truthy value is expected:

- Numbers and `char`s are truthy when they're `!= 0`.
- Booleans are truthy when they're `true`.
- Pointers are truthy when they're non-null.
- Arrays are truthy when their length is `> 0`.
- Every other type, including custom ones, has no implicit truthyness and must be compared explicitly.

### Conditionals

Conditional execution is expressed through the `if` keyword, followed by a parenthesized expression and a block. The arm runs when the expression is truthy:

```
if (<expr>) {
    // ...
}
```

Optionally followed by `else`, which runs when the condition is false. An `else` can itself be another `if`, chaining multiple conditions:

```
if (<expr>) {
    // ...
} else if (<expr>) {
    // ...
} else {
    // ...
}
```

### Functions

Functions are declared through the `fn` keyword followed by their name:

```
fn function() {
    // do something
}
```

They can have an arbitrary number of parameters in the format `t: T`, separated by commas:

```
fn function(a: A, b: B, c: C) {
    // do something
}
```

where `A`, `B` and `C` are concrete types.

They can also return values. The return type `T` is annotated through `-> T`, while the value to return follows the keyword `return`.

```
fn function() -> T {
    // do something
    return t;
}
```

Functions can be forward-declared: that way they can be declared first and implemented later.

```
// a.sie
fn f1();
fn f2() -> T;
fn f3(t: T) -> T;

// b.sie
fn f1() {
    // ...
}

fn f2() -> T {
    // ...
    return t;
}

fn f3(t: T) -> T {
    // ...
    return t;
}
```

#### Entry point

A program's entry point is a function named `main`, which can take one of a few forms.

Taking no parameters and returning nothing implicitly returns `0`:

```
fn main() {
    // ...
} // equivalent to returning 0
```

Taking `argc` and `argv` is the C-style form, giving direct access to the raw count and pointer:

```
fn main(argc: i32, argv: char**) {
    // ...
}
```

Taking a single `char*[]` parameter gets `args` as a ready-made array, with `argv[0]`'s program name still included:

```
fn main(args: char*[]) {
    // ...
} // equivalent to prefixing the body with 'let args: char*[] = {argv, argc as u64};'
```

Any of these forms may also return `i32` explicitly, in which case the returned value becomes the program's exit code instead of `0`.

#### Const parameters

A parameter can be marked `const` by prefixing its type, through `const T` instead of `T`. This is unrelated to `@const` constant expressions: here it's a contract between caller and callee rather than a compile-time substitution. `a: T` and `a: const T` are represented identically; the latter is simply the callee's promise not to mutate `a`. A `const` parameter cannot be reassigned, and no mutating method can be called on it.

```
fn f(a: const A) {
    // a cannot be reassigned or mutated through here
}
```

This is also how a method's receiver declares whether it mutates the struct: a mutating method takes `self: &S`, while one that only reads from it takes `self: const &S`.

#### Generic functions

Functions are generic when their name is followed by an arbitrary number of placeholder types `A`, `B`, etc. enclosed by `<>` and separated by commas.

```
fn f<T>(t: T); // a generic function that receives a parameter of type T,
               // where T is a generic type that can be replaced by any
               // concrete type at compile time

fn f<T>() -> T; // a generic function that returns a value of type T
                // where T is a generic type that can be replaced by any
                // concrete type at compile time

fn f<T, U>(t: T, u: U); // a generic function that receives parameters of type T and U,
                        // where T and U are generic types that can be replaced by any
                        // concrete types at compile time

fn f<T, U>(t: T) -> U; // a generic function that receives a parameter of type T and
                       // returns a value of type U, where T and U are generic types
                       // that can be replaced by any concrete types at compile time
```

#### Extern

Functions can be decorated with `@extern` to indicate that they're going to be resolved at link time. Extern functions must follow C's ABI and can only use C-compatible types.

```
@extern fn printf(fmt: char*, ...);
@extern fn malloc(size: u64) -> opaque*;
@extern fn free(ptr: opaque*);
```

#### Asm

Functions can be decorated with `@asm` to indicate that their body is written in assembly instead of Sie code.

```
@asm
fn bswap32(value: u32) -> u32 {
    rev ${out:w}, ${value:w}
}
```

Inside the body, `${name}` interpolates the register holding the param `name`, while `${out}` represents the return value. An optional modifier can follow the name through `:`, e.g. `${value:w}` to use the 32-bit view of the register.

The decorator optionally accepts the registers and state clobbered by the assembly:

```
@asm("x0", "memory")
fn f() {
    // ...
}
```

### Types

#### Builtin types

- Signed integers: `i8`, `i16`, `i32`, `i64`.
- Unsigned integers: `u8`, `u16`, `u32`, `u64`.
- Floats: `f32`, `f64`.
- Booleans: `bool`.
- Characters: `char`.

Unlike other languages, there's no `void`. For opaque pointers you can use `opaque*`.

Any pointer can be used where an `opaque*` is expected, decaying to it contextually; arrays reach it through their data pointer. The explicit cast is also allowed.

```
@extern fn memcpy(dest: opaque*, src: opaque*, count: u32) -> opaque*;

let src: i32[] = [7, 8, 9];
let dst: i32[] = [0, 0, 0];

memcpy(dst, src.data, 12); // both decay to opaque*
let p: opaque* = dst as opaque*;
```

The reverse direction never happens implicitly: an `opaque*` only becomes a typed pointer through an explicit cast.

```
@extern fn malloc(size: u64) -> opaque*;

let values: i32* = malloc(12) as i32*;
```

Signed and unsigned values cannot be mixed in the same operation: comparing or combining an `i32` with a `u32` is a compile-time error. Integer literals adapt to either side.

```
let s: i32 = 1;
let u: u32 = 2;

let a: i32 = s + 1; // ok, the literal adapts
let b: u32 = u + 1; // ok, the literal adapts
let c: bool = s < u; // error: cannot mix signed and unsigned operands
```

#### Implicit widening

A value of an `iN`, `uN`, or `fN` type widens implicitly when it's assigned or passed to a larger type, as long as the prefix is kept: `iN` to a wider `iM`, `uN` to a wider `uM`, `fN` to a wider `fM`. Signed values sign-extend, unsigned values zero-extend, and floats extend.

```
let a: u8 = 0;
let b: u64 = a; // implicit widening, equivalent to let b: u64 = a as u64;
```

Crossing prefixes — between signed, unsigned, and floats — or narrowing to a smaller width is never implicit and requires an explicit cast:

```
let a: i16 = 0;
let b: i8 = a;  // error: narrowing
let c: u32 = a; // error: signed to unsigned
let d: f32 = a; // error: integer to float
```

#### Pointers

A pointer to `T` is written `T*`. Indexing a pointer with `ptr[i]` accesses the `i`th `T` past it, C-style, and can be read or assigned to.

```
let ptr: i32*;

let first: i32 = ptr[0]; // read
ptr[1] = 5;              // write
ptr[1] += 5;             // compound write
```

Indexing a pointer to a struct reaches into the indexed element's fields the same way:

```
let points: Point*;
points[0].x = 5;
```

#### Arrays

Arrays are collections of same-type values. They are represented by `X[]` and their internal representation is always `{X*, u64}`, where `X*` is a pointer to `X` and `u64` is the number of elements. These are exposed as the members `data` and `length`, accessed like a struct's:

```
let arr: i32[];

let ptr: i32* = arr.data;   // the backing pointer
let n: u64 = arr.length;    // the element count
```

An array can be indexed directly, reading or writing the `i`th element through its backing data:

```
let first: i32 = arr[0]; // equivalent to arr.data[0]
arr[1] = 5;
```

Arrays can be initialiazed with elements `a`, `b`, etc. enclosed by `[]` and separated by commas.

```
let arr: i32[] = [1, 2, 3];
```

Elements can themselves be pointers or arrays, so an array literal can build an array of strings or an array of arrays.

```
let cmds: char*[] = ["ls", "cd", "cp"];
let msgs: char[][] = ["hello", "world"];
```

They can also be initialized with a pointer `ptr` and length `n` enclosed by `{}` and separated by commas.

```
let ptr: i32* = [1, 2, 3];
let n: u64 = 3;
let arr: i32[] = {ptr, n};
```

It is possible to cast an array `X[]` to a pointer `X*`. When an array is used where a plain pointer is expected, it lowers to its `X*` contextually.

```
fn f(value: i32*);

let arr: i32[] = [1, 2, 3];
f(arr); // equivalent to f(arr as i32*);
```

An array can be sliced with `arr[from:to]`, where either bound can be omitted: `from` defaults to `0` and `to` defaults to `arr.length`. Slicing yields an `X[]` view over the same backing data, not a copy.

```
let arr: i32[] = [1, 2, 3, 4, 5];

arr[1:];  // [2, 3, 4, 5]
arr[:3];  // [1, 2, 3]
arr[1:3]; // [2, 3]
```

#### String literals

String literals are arrays of type `char[]`. They can be initialized with characters enclosed by `""`.

```
let msg: char[] = "Hello";
```

Just like any other array, they can initialized by a pair `{ptr, n}`:

```
let ptr: char* = "Hello";
let n: u64 = 5;
let msg: char[] = {ptr, n};
```

They are null-terminated for C compatibility, but their length does not include the null character. This is why `char` is its own type instead of an alias of `i8` or `u8`: a `char[]` carries string semantics that a plain byte array does not.

Casting between `i8[]`/`u8[]` and `char[]` automatically handles the length change, but assumes that the underlying pointer is null-terminated.

#### References

References to a type `T` are represented by `&T`. References cannot be dereferenced, meaning that you can't obtain the address where the value is stored through the `&` operator.

References cannot type a variable.

```
let t: &T; // invalid
```

As function params, they indicate that the value is passed by reference instead of by value. Internally they are represented by a hidden pointer.

```
fn add(a: &i32, b: i32) {
    a += b;
}

fn main() {
    let a: i32 = 1;
    let b: i32 = 2;
    
    add(a, b);
    
    // now a holds the value 3
}
```

#### Function references

Function references represent references to functions with a given signature. Their type is written like a function declaration, without a name and with parameter types only:

```
let fp1: fn();
let fp2: fn() -> T;
let fp3: fn(A, B) -> T;
```

They can also be used as function parameter types:

```
fn func(f: fn());
fn func(f: fn() -> T);
fn func(f: fn(A, B) -> T);
```

A function's name is a reference to it, and a reference is called like any function:

```
fn double(x: i32) -> i32 {
    return x * 2;
}

fn apply(f: fn(i32) -> i32, x: i32) -> i32 {
    return f(x);
}

fn main() -> i32 {
    let fp: fn(i32) -> i32 = double;
    return apply(fp, 21); // 42
}
```

#### Type aliases

Type aliases give an existing type expression a new name. They're declared through the `type` keyword followed by their name, `=`, and the aliased type expression, ending in `;`:

```
type <name> = <type expr>;
```

```
type string = char[];
type fnc1 = fn();
type fnc2 = fn() -> bool;
type fnc3 = fn(char[]);
```

#### Generic type aliases

Type aliases are generic when their name is followed by an arbitrary number of placeholder types `A`, `B`, etc. enclosed by `<>` and separated by commas.

```
type cmp<T> = fn(T, T) -> bool;
type fnc<T, U> = fn(T, U);
type fnc<T, U> = fn(T) -> U;
```

#### Type casting

Numeric values can be explicitly converted to another numeric type through the `as` keyword, followed by the target type. This is the escape hatch for conversions that don't happen implicitly: narrowing, and crossing between signed, unsigned, and float.

```
x as Y
```

```
let a: i32 = 10;
let b: f64 = a as f64;  // integer to float: 10.0
let c: u8 = 300 as u8;  // narrowing: truncated to 44
let d: u32 = -1 as u32; // signed to unsigned: reinterpreted to 4294967295
```

Widening integers keep their value (signed types sign-extend, unsigned zero-extend), narrowing integers truncate, and float conversions round. Casting a value to its own type is a no-op:

```
let e: i32 = a as i32; // e holds the same value as a, unchanged
```

`as` binds tighter than any binary or comparison operator, so a cast applies only to the value right before it:

```
let f: u32 = a as u32 + 1; // (a as u32) + 1
```

### Enums

Enums are collections of constants. They are declared through the keyword `enum` followed by their name. Their members are declared by name, separated by commas.

```
enum name {
    ABC,
    DEF,
}
```

Optionally, you can define a specific value for any of their members through `= <value>` after their name.

```
enum name {
    ABC,
    DEF = 5,
}
```

Members are assigned values automatically, starting at 1 and increasing by 1 for each subsequent member. Setting a specific value for a member changes the counter for the following ones, which then keep increasing from there.

```
enum name {
    ABC, // = 1
    DEF = 5,
    GHI, // = 6
}
```

They can be untyped, or have a specific underlying type `T` declared through `: T` after their name:

```
enum name: T {
    // ...
}
```

They can be used as types:

```
let flag: my_enum;
```

where their internal representation is the type `T` defined in their declaration, or `i32` in the case of untyped enums.

### Structs

Structs are containers that can hold structured data of multiple types. They're declared through the keyword `struct` followed by their name, while their members are declared by their name followed by `: T`, where `T` is their type, and separated by semi-colons.

```
struct S {
    a: A;
    b: B;
    // etc...
}
```

They can be used as types:

```
fn f(s: S); // a function that receives a param of type S
fn f() -> S; // a function that returns a value of type S
let s: S; // a variable that holds a value of type S
```

Structs can be forward-declared: declared with no body at all. This is mainly useful for opaque structs, whose fields are never given and which are only ever handled through a pointer.

```
struct Handle; // forward declaration, never given a body

fn open() -> Handle*;
fn close(h: Handle*);
```

#### Generic structs

Structs can be generic when their name is followed by `<X, Y, ...>`, where `X` and `Y` are arbitrary types.

```
struct List<T> {
    data: T*;
    length: u64;
    capacity: u64;
}
```

To use a concrete version of a generic struct you have to use the struct's name plus `<A>`, where `A` is a concrete type.

```
fn f(lst: List<i32>); // a function that receives a param of type List<i32>
fn f() -> List<i32>; // a function that returns a value of type List<i32>
let lst: List<i32>; // lst is a variable that holds a value of type List<i32>
```

### Methods

Structs can have methods, which are a special type of function that acts
on a specific struct type.

Similar to regular functions, they're declared through the `fn` keyword,
but their name is marked by the prefix `S::`, where `S` is the struct
they're registered to.

Their first param is the receiver and is always a reference, meaning that
the method acts on the instance itself and not on a copy.

```
fn S::method(self: &S) {
    // ...
}
```

If the method does not mutate the instance, the receiver should be declared `self: const &S` instead, so it can also be called on a `const S`. Calling a mutating method (`self: &S`) on a `const` instance is an error.

```
fn S::read(self: const &S) -> T {
    // cannot mutate self here
    return t;
}
```

They can be called by passing an instance `s` to their fully qualified name:

```
S::method(s);
```

Or simply by:

```
s.method();
```

Just like regular functions, they can return a value of type `T`:

```
fn S::method(self: &S) -> T {
    // ...
    return t;
}
```

And have multiple params:

```
fn S::method(self: &S, a: A, b: B) {
    // ...
}
```

#### Generic methods

Just like functions, methods can be generic when they declare an arbitrary number of placeholder types `A`, `B`, ... after their name, enclosed by `<>` and separated by commas.

```
fn S::method<A, B, ...>(self: &S, a: A, b: B, ...) {
    // ...
}
```

#### Methods of a generic struct

Given a generic struct `S` with generic type params `A`, `B`, etc.

```
struct S<A, B, ...> {
    // ...
}
```

any of its methods that act on any of the possible types `A`, `B`, etc. must also have those placeholders in their prefixes:

```
fn S<A, B, ...>::method(self: &S<A, B, ...>) {
    // ...
}
```

#### Generic methods of a generic struct

Methods of a generic struct can also be generic, meaning they can have their own placeholder types `X`, `Y`, ..., placed after their name, enclosed by `<>` and separated by commas.

```
fn S<A, B, ...>::method<X, Y, ...>(self: &S<A, B, ...>, x: X, y: Y, ...) {
    // ...
}
```

### Interfaces

Sie has no inheritance: interfaces are its only mechanism for abstract typing. They define an abstract object: a set of fields and actions that any struct implementing them must provide. They're declared through the `interface` keyword followed by their name, with their fields declared the same way as a struct's:

```
interface Named {
    name: char[];
}
```

They can be used as types, standing for any struct that implements them:

```
fn f(n: Named); // a function that receives any struct implementing Named
```

Their actions are declared the same way as struct methods, but with the interface as the receiver's type and no body, describing the signature a struct must implement:

```
fn Named::greet(self: &Named) -> char[];
```

Interface conformance is nominal: a struct only implements an interface when it says so, through `: I` after the struct's name. Since there's no inheritance, `:` in that position always introduces interfaces. Implementing one still requires declaring its fields and providing its actions:

```
struct Person: Named {
    name: char[];
}

fn Person::greet(self: &Person) -> char[] {
    return self.name;
}
```

A struct can implement more than one interface, separated by commas:

```
struct Person: Named, Aged {
    // ...
}
```

#### Generic interfaces

Interfaces can be generic just like structs, when their name is followed by `<T>`. An interface with no fields of its own can be declared without a body, ending in `;` instead:

```
interface Iterable<T>;

fn Iterable<T>::iterator(self: &Iterable<T>) -> Iterator<T>;

interface Iterator<T>;

fn Iterator<T>::next(self: &Iterator<T>) -> Result<T, IterationError>;
```

### Error handling

Errors are handled through `Result<V, E>`, a builtin struct that contains a return value or an error; and `Result<E>`, a builtin struct that only contains an error value.

```
struct Result<V, E> {
    value: V;
    error: E;
}

struct Result<E> {
    error: E;
}
```

## Concepts

### Scopes

They are the context where stuff happens.
