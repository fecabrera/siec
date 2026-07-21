# sielang

Sie is a a modern C-flavored language with minimal syntax, a strong type system and type inference. The main goal of this project is to simplify the coding experience for programmers while providing full low-level control.

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

Precompiled object files and static libraries may be given alongside the sources; they skip compilation and link into the executable (and `--run` resolves their symbols too, unpacking an archive's members into the JIT):

```
siec main.sie file1.o file2.o -o main
siec main.sie libfoo.a -o main
```

- `-o <path>` names the output executable, `a.out` by default.
- `-c` compiles to an object file without linking, named after the source (`main.sie` → `main.o`) unless `-o` says otherwise.
- `-I <dir>` adds a directory to the include search path. The `lib/` directory next to each source file is always searched.
- `-O <n>` sets the optimization level, cc-style: `-O0` (the default) emits code as generated, and `-O1` through `-O3` run LLVM's standard optimization pipeline. It applies to every output form, including executables, objects, `--emit-llvm`, `--emit-asm`, and `--run`.
- `-g` emits DWARF debug info, cc-style: every instruction maps to its source line, and every function, parameter, and variable is described with its type. A `-g` build debugs at source level in lldb or gdb: breakpoints by file and line, stepping, `bt` with Sie lines, and `frame variable` showing struct fields, arrays as their `{data, length}` pair, and unions. Debug at `-O0`, where nothing is reordered; on macOS, keep the `.o` the build leaves next to the executable, since the debugger reads the DWARF from it.
- `-l <lib>` links against a library, passed through to the linker: `-l m` links the C math library. Under `--run`, the library is loaded into the process instead, its symbols resolvable the same way.
- `-L <dir>` adds a directory to the library search path.
- `--target <triple>` compiles for a target triple instead of the host (`x86_64-unknown-linux-gnu`, say). It aims everything at the target: the object code, the [target constants](#target-constants), and every `sizeof`. Cross-built objects are best taken out with `-c`, since linking still runs the host's `cc`; `--run` only accepts the host's own triple, as the JIT runs in-process.
- `--emit-llvm` prints the LLVM IR and exits, without building.
- `--emit-asm` prints the target's assembly and exits, without building.
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

Optionally, you can pick specific members of a module through `{}` and `from`, which brings them into scope unqualified; a multi-line list may close with a trailing comma:

```
import { f } from module.submodule;

import {
    f,
    g,
} from module.submodule;
```

Both members and modules can be aliased through `as`:

```
import { f as g } from module.submodule;
import module.submodule as sub;
```

Every file is a module: `import a.b` names the file `a/b.sie`, searched for in the importing file's directory first, then the working directory, and finally the include path. Each file loads once however many times it's imported, so import cycles are fine.

A module offers every one of its top-level declarations except its `@static` ones, which stay its own; importing a name it doesn't offer is an error. Because imports are resolved before compilation evaluates anything, an `import` cannot sit inside an `@if` block.

An imported module's members stay inside its namespace: they're reachable only through their qualified spelling (or a member import), never unqualified. A file's unqualified view holds its own declarations, its member imports, whatever it pulled in with `@include`, and the compilation unit's: the source files given together on the command line share their names, C-style.

Types scope the same way: a module's structs, enums, and aliases are reachable through their qualified spelling in any type position (`let pkg: package.Package;`, `shapes.Box<i32>`, casts and `sizeof` included) or unqualified through a member import (`import { Point, Box as Crate } from shapes;`). Enum members follow their enum: `shapes.Color::RED` qualified, or `Color::RED` once `Color` is member-imported. A type's name written without either is an error; only types *inferred* across the boundary (a call's return type, say) flow without their module's name in view.

#### Include

`@include("path")` pulls a specific `.sie` file directly into the current file, searching the include path:

```
@include("libc/stdio")
```

The path resolves against, in order: the including file's own directory, any `-I` directories, the `lib/` directory beside each source, the working directory, and the `lib/` directory under it. The last two let a project compile from its root wherever its sources sit.

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

When an initializer is given, the type annotation can be omitted and the variable adopts the initializer's type:

```
fn f() -> T;

let a = f(); // a has an implicit `: T`
```

Inference follows the value: variables, calls, casts, fields, and elements carry their declared types; comparisons yield a `bool`; and bare literals take their usual defaults (`i32`, `f64`, `char*`). An initializer with no fixed type of its own, like an array literal, still needs the annotation.

A declaration with neither a type nor an initializer has nothing to size the variable by and is rejected.

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

`@const` also declares compile-time macros, taking parameters between `(...)`. A call to `name` substitutes it with the block, `emit` optional since the macro doesn't have to produce a value:

```
@const name(param1, param2) {
    // ...
    emit <expr>; // optional
}
```

#### Target constants

The compiler defines a set of constants describing the compilation target, taken from the target triple: the host's, or the one `--target` names. `TARGET_OS` and `TARGET_ARCH` hold the current target's families, and one constant names each family they can match:

| OS | Architecture |
|---|---|
| `OS_DARWIN` | `ARCH_X86_64` |
| `OS_LINUX` | `ARCH_AARCH64` |
| `OS_WINDOWS` | `ARCH_RISCV64` |
| `OS_NONE` | `ARCH_UNKNOWN` |
| `OS_UNKNOWN` | |

`OS_NONE` marks bare-metal targets (a triple like `riscv64-unknown-none-elf`); the unknowns catch anything the compiler doesn't classify.

```
case (TARGET_OS) {
    when OS_DARWIN:  setup_darwin();
    when OS_LINUX:   setup_linux();
    when OS_WINDOWS: setup_windows();
    else:            fail("unsupported platform");
}

@const PAGE_ALIGNED = TARGET_ARCH == ARCH_AARCH64;
```

They behave like any other `@const` (usable in constant expressions, case arms, and array sizes), except that redeclaring one is an error.

### Conditional compilation

`@if` compiles a group of top-level declarations only when a compile-time condition holds, with an optional `@else`:

```
@if (<const expr>) {
    // ...
} @else {
    // ...
}
```

The condition is a constant expression: literals, `@const` names, enum members, `sizeof`, arithmetic, comparisons, and `and`/`or`/`not`. The unchosen branch is skipped entirely, never parsed into the program, so its declarations may collide with the chosen one's:

```
@if (TARGET_OS == OS_DARWIN) {
    @extern fn errno_location() -> i32*;
} @else {
    @extern fn errno_location() -> i32*;
}
```

`@else @if` chains conditions, first match winning:

```
@if (TARGET_OS == OS_LINUX) {
    // ...
} @else @if (TARGET_OS == OS_DARWIN) {
    // ...
} @else {
    // ...
}
```

A branch may hold any top-level declaration (functions, structs, enums, globals, constants, type aliases) including further `@if` blocks, and a constant declared in a chosen branch is visible to the conditions after it. The one exception is `@include`, which joins the program before any condition can be evaluated and so cannot be conditional.

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

Each arm is its own scope, like any block: variables declared inside an arm end with it, while assignments to outer variables persist.

```
let a: i32 = 1;

if (a == 1) {
    let b: i32 = 41; // ends with the arm
    a = a + b;       // the outer a keeps the write
}

// a is 42; b no longer exists
```

When a body is a single statement, the braces may be omitted; the statement still forms an arm scope of its own. This goes for `else`, `while`, and `for` bodies alike:

```
if (a == 1) a += 1;
else a = 0;
```

#### Case

`case` matches a subject against a series of `when` arms, running exactly the first one whose value equals it. There is no fall-through: after an arm runs, control moves past the case. An optional `else` arm, last, runs when nothing matched; without one, an unmatched subject just moves on.

```
case (op) {
    when Op::ADD:
        result = a + b;
    when Op::SUB:
        result = a - b;
    else:
        result = 0;
}
```

The subject is evaluated once. `when` values are ordinary expressions, compared with the subject by equality in order, and each arm's statements run in a scope of their own, up to the next `when`, `else`, or the closing brace.

A `when` may list several comma-separated values; any of them selects the arm:

```
case (c) {
    when 'a', 'e', 'i', 'o', 'u':
        vowels += 1;
    else:
        others += 1;
}
```

#### Ternary

`cond ? then : else` is the expression form of a conditional: it evaluates only the chosen arm and produces its value. It binds looser than any other operator and chains right, C-style:

```
let max: i32 = a > b ? a : b;
let grade: i32 = n > 9 ? 1 : n > 3 ? 2 : 3;
```

Both arms must produce the same type; literal arms adapt to the context like any literal.

### Loops

Repetition is expressed through the `while` keyword, followed by a parenthesized expression and a block. The body runs while the expression is truthy, checked before each iteration:

```
while (<expr>) {
    // ...
}
```

Like an if arm, the body is its own scope, a fresh one on each iteration, so a variable declared inside the body doesn't carry over to the next pass:

```
let i: i32 = 0;

while (i < 10) {
    let doubled: i32 = i * 2; // born and gone each iteration
    i += 1;
}
```

A single-statement body may drop the braces, keeping its own scope:

```
while (i > 0) i -= 1;
```

The `for` keyword drives a loop through three parts: an init statement run once, a condition checked before each pass, and a step statement run after each:

```
for (let i: i32 = 0; i < n; i += 1) {
    // ...
}
```

The whole loop is its own scope (the init's variable lives exactly as long as the loop), and the body behaves like a while's, fresh on each iteration.

A single-statement body may drop the braces here too:

```
for (let i: i32 = 0; i < n; i += 1) total += i;
```

Unlike other languages, there are no increment or decrement operators (`i++`, `i--`); this is intentional, and a step is written `i += 1`.

#### Foreach

`foreach (v : iterable)` walks a collection's elements through [the iteration interfaces](#the-iteration-interfaces): the iterable hands out its iterator (`iterator()`, or itself when it already is one), and each pass binds `v` to the element `next()` references.

```
foreach (v : nums) {
    total += v;
}
```

`v` is a true [reference](#references) into the collection, not a copy, exactly like a reference parameter: assigning it writes the element in place, and calling a mutating method on it mutates the collection's own.

```
foreach (v : nums) {
    v = v * 2;    // doubles the array's elements themselves
}
```

Anything `Iterable<T>` works - [arrays come iterable](#the-iteration-interfaces) - and `break`/`continue` steer the loop like any other. Iterating a bare iterator value walks a copy of its state, from wherever it stands. A `const` array iterates too, through the builtin `ConstArrayIterator<T>`: its elements read as `const &T`, so the contract follows them and writing one is an error.

#### Enumerate

The builtin `enumerate(x)` wraps an Iterable (or an iterator) in an iterator of `{index: u64, value: T}` pairs, counting from zero:

```
foreach (e : enumerate(nums)) {
    printf("%llu: %d\n", e.index, e.value);
}
```

`value` is a copy of the element, not a reference into the collection; a declared function named `enumerate` takes precedence over the builtin.

#### Break and continue

`break` leaves the innermost enclosing loop; `continue` jumps to its next pass. In a `for`, `continue` lands on the step, so the loop always advances:

```
while (true) {
    let n: u64 = fread(buffer, 1, CHUNK, src);
    if (n == 0) break;
    // ...
}

for (let i: i32 = 0; i < n; i += 1) {
    if (i % 2 == 0) continue; // steps to i + 1
    // ...
}
```

Both flush the deferred statements of the scopes they leave, innermost first, like an early `return` does on its way out of a function.

A deferred statement cannot `break` or `continue` the loop it flushes inside of, but a loop of its own is free to:

```
while (running) {
    defer { break; }    // error: a deferred statement cannot break

    defer {
        while (drain()) {
            if (done()) break; // fine: it steers its own loop
        }
    }
}
```

### Blocks

Code enclosed by `{}` is a block, with its own scope:

```
{
    // ...
}
```

A block can also be used as a value, in which case it must produce one through the `emit` keyword. An `emit` ends the block where it runs, the way a `return` ends a function:

```
a = {
    // ...
    emit <expr>;
};
```

This is how a block initializes a variable:

```
let a: T = {
    // ...
    emit <expr>;
};
```

### Defer

`defer` pushes an expression or block onto a stack that runs at the end of the current scope, exactly before it returns:

```
defer <expr>;
defer func();
defer {
    // ...
}
```

This is commonly used to release a resource right next to where it's acquired:

```
fn f() -> i32 {
    let a: T* = malloc(1) as T*;
    defer free(a);
    return 0;
}
```

The deferred call runs after the return value is computed but before control actually leaves the function:

```
f:
    mov $a, malloc(1)
    mov $out, 0
    call free(a)
    ret
```

With more than one `defer` in the same scope, they run in reverse order, last deferred first.

A deferred block cannot `defer` directly: the stack it would push onto is the very one being flushed. A scope of its own inside the block can, running as that scope ends:

```
defer {
    defer free(a);     // error: a defer cannot hold another defer directly
}

defer {
    {
        defer free(a); // fine: runs when the inner scope ends
        // ...
    }
}

defer {
    for (let i: u64 = 0; i < n; i += 1) {
        defer release(i); // fine: the loop body is a scope, flushed each pass
    }
}
```

For the same reason, a deferred statement cannot `return`, `emit`, `break`, or `continue` its surroundings; each would cut through the flush that's already underway.

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

Since `const` is not part of the type, a `T` passes directly where a `const T` is expected. The reverse never happens: a `const` pointer or array is never used where a mutable one is expected, not implicitly and not through a cast. The contract follows the value, through pointer fields read from a `const` struct, through indexing, and through inferred `let`s:

```
fn f(s: const char*) {
    let t = s;         // t is const char* too
    take(s);           // error: take wants a mutable char*
    take(s as char*);  // error: const cannot be cast away
}
```

Copies of non-aliasing values discard the contract naturally: a `const i32` argument is just a value, and copies of it are the caller's own. `const` also works anywhere a type is written: on `let` declarations, struct fields, and return types.

This is also how a method's receiver declares whether it mutates the struct: a mutating method takes `self: &S`, while one that only reads from it takes `self: const &S`.

#### Default arguments

A parameter can declare a default value with `= expr`, letting calls omit its argument:

```
fn greet(name: const char*, times: i32 = 1) {
    // ...
}

greet("sie");       // times is 1
greet("sie", 3);
```

Only the last parameters can carry defaults: they fill a call's omitted trailing arguments, so a parameter after a defaulted one needs a default too.

The default is any expression, evaluated at each call as if written there, but resolved in the declaring file: it can reference that file's constants, globals, and functions without the caller importing them. [Methods](#methods) take defaults the same way, from either call form, and a [constructor](#constructors) fills `init`'s:

```
fn List<T>::init(self: &List<T>, capacity: u64 = DEFAULT_CAPACITY) { ... }

let l = List<i32>();    // capacity is DEFAULT_CAPACITY
```

A call through a [function reference](#function-references) passes every argument: the reference's `fn(...)` type carries no defaults.

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

A call instantiates the function for its concrete types, compiled once per argument list. The type arguments are inferred from the value arguments (`identity(n)` on an `i32` compiles `identity<i32>`) by matching each parameter's shape against its argument (`items: T*` against an `i32*` binds `T` to `i32`), with literals defaulting like they do in any untyped context.

In a typed context (a declared return type, an annotated `let`, an argument's parameter) the expected type also drives inference, binding what the arguments cannot: `return Ok(v);` names both of `Result<V, E>`'s parameters from the return type. Where the expected type and an argument both speak, the expected type wins and the argument coerces to it. When nothing pins a parameter down (`fn empty<T>() -> T*` called bare), spell the arguments explicitly:

```
let p = empty<i32>();
let x = identity<i64>(5);
```

Same-named generic functions with different type-parameter counts coexist, like [generic structs](#generic-structs) of different arities: the call's shape (its explicit `<...>` count, its argument count, and what resolves) picks the template.

Generic functions may recurse and call one another, and their return types may name generic structs (`fn make<T>(t: T) -> Box<T>`). The same modifier rule as [generic structs](#generic-structs) applies to type arguments, and a template nobody calls compiles to nothing. `@extern` functions cannot be generic: they name one foreign symbol.

A generic function also works as a [function reference](#function-references): `identity<i32>` outside a call is the instance's function value, and a bare generic name bound to a function-typed context (a `fn(...)` annotation, parameter, or [generic alias](#generic-type-aliases) of one) picks its arguments by unifying the template's signature with the target:

```
let g = identity<i32>;             // explicit instance
let h: fn(i64) -> i64 = identity;  // T unified from the annotation
apply(identity, 40);               // T unified from apply's parameter type
```

Qualified spellings work the same way: `util.identity<i32>` and a bare `util.identity` in a function-typed context both resolve through the module binding.

#### Extern

Functions can be decorated with `@extern` to indicate that they're going to be resolved at link time. Extern functions must follow C's ABI and can only use C-compatible types.

```
@extern fn printf(fmt: char*, ...);
@extern fn malloc(size: u64) -> opaque*;
@extern fn free(ptr: opaque*);
```

Struct and union values cross the boundary by value the way C's do, in both directions: the compiler lowers parameters and returns to the target's C calling convention (registers for the small ones, memory for the large), so a C function taking or returning a struct is declared and called naturally:

```
struct div_t {
    quot: i32;
    rem: i32;
}

@extern fn div(numer: i32, denom: i32) -> div_t;

let r = div(87, 2); // returns exactly as C would
r.quot;             // 43
```

`@extern let` declares a global variable the same way: its storage is defined and initialized outside the program, so it takes no initializer. It reads and assigns like any variable, and may hold a function reference, called through like a local one:

```
@extern let environ: char**;
@extern let MPD_MINALLOC: i64;
@extern let mpd_traphandler: fn(mpd_context*);
```

#### Symbol

`@symbol("name")` decouples a function's Sie name from its module symbol: the function links and emits under the given symbol, while the program calls it by its Sie name. Combined with `@extern`, it binds a foreign symbol behind a name of your choosing; combined with [conditional compilation](#conditional-compilation), one name covers a symbol that differs by platform:

```
@if (TARGET_OS == OS_DARWIN) {
    @extern @symbol("__error") fn errno_location() -> i32*;
} @else {
    @extern @symbol("__errno_location") fn errno_location() -> i32*;
}
```

`@extern let` globals take it the same way, binding an outside data symbol behind a Sie name:

```
struct FILE;

@if (TARGET_OS == OS_DARWIN) {
    @extern @symbol("__stdoutp") let stdout: FILE*;
} @else {
    @extern @symbol("stdout") let stdout: FILE*;
}
```

It also works on defined functions, exporting them under the chosen symbol. `main` cannot be renamed (the C runtime looks it up by name), and `@symbol` cannot combine with `@static`, whose symbol is the compiler's to mangle.

#### Inline

Functions can be decorated with `@inline` to inline them into every caller. Unlike C's `inline`, this is not a hint: the function is always inlined, even at `-O0`.

```
@inline fn square(n: i32) -> i32 {
    return n * n;
}
```

#### Static

Functions can be decorated with `@static` to make them local to their file: no other file sees them, and every file may reuse the name for a static of its own. This is the home for a file's private helpers.

```
@static fn helper() -> i32 {
    // only callable from this file
}
```

Decorators stack, so `@static @inline fn` is both, except for `@extern`, whose function has no body for the others to act on.

`@static let` declares a file-local global variable the same way: one storage location shared by every call, visible only to its own file. Its initializer, when given, must be a compile-time constant; without one it starts at zero, C-style.

```
@static let count: i32 = 0;

fn bump() -> i32 {
    count += 1;
    return count;
}
```

#### Asm

Functions can be decorated with `@asm` to indicate that their body is written in assembly instead of Sie code.

```
@asm
fn bswap32(value: u32) -> u32 {
    rev ${out:w}, ${value:w}
}
```

Inside the body, `${name}` interpolates the register holding the param `name`, while `${out}` represents the return value. A bare `$name` works too; the braces are only needed to attach a modifier through `:`, e.g. `${value:w}` to use the 32-bit view of the register. Any other `$` (an x86 immediate like `$42`, say) passes through as the assembly's own.

`@clobbers(...)` declares the registers and other state the assembly clobbers beyond its own operands:

```
@asm @clobbers("x0", "memory")
fn f() {
    // ...
}
```

`@asm` also works as an inline block, embedding assembly in an expression or statement position instead of taking over a whole function. Values from the enclosing scope pass in through a parenthesized argument list, each interpolated inside the block by its own name, exactly like a decorated function's params:

```
@asm { /* no operands */ }

@asm (x, y) {
    add ${out:w}, ${x:w}, ${y:w}
}

@asm @clobbers("x0", "memory") { /* ... */ }

@asm @clobbers("x0", "memory") (x, y) { /* ... */ }
```

An inline block produces a value when its argument list is followed by `-> T`, `${out}` standing for the result the same way it does in a decorated function:

```
let sum: i32 = @asm (x, y) -> i32 {
    add ${out:w}, ${x:w}, ${y:w}
};

let masked: i32 = @asm @clobbers("x0", "memory") (x, y) -> i32 {
    // ...
};
```

### Types

#### Builtin types

- Signed integers: `i8`, `i16`, `i32`, `i64`.
- Unsigned integers: `u8`, `u16`, `u32`, `u64`.
- Floats: `f32`, `f64`.
- Booleans: `bool`.
- Characters: `char`.

Integer literals may also be written in hexadecimal with the `0x` prefix:

```
let mask: u32 = 0xFF00;
```

Float literals are written with a `.` between their digits, adopting the float type of their context like integer literals do:

```
let pi: f64 = 3.14159;
let half: f32 = 0.5;
```

Char literals hold exactly one byte between single quotes, decoding the same escape sequences as strings. Their type is `char`, never `i8` or `u8`:

```
let c: char = 'a';
let end: char = '\0';
let hex: char = '\x41'; // 'A'
```

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

Operands widen the same way: when the two sides of an arithmetic operation or comparison share a prefix but differ in width, the narrower one meets the wider.

```
let total: u64 = 100;
let step: u32 = 7;

let rest: u64 = total - step; // step widens to u64
if (step < total) { }         // and in comparisons
```

Crossing prefixes (between signed, unsigned, and floats) or narrowing to a smaller width is never implicit and requires an explicit cast:

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

The `&` operator takes the address of a value, yielding a `T*`. It applies to anything assignable: a variable, a struct field, or an indexed element.

```
let x: i32 = 1;
let p: i32* = &x;

p[0] = 5; // writes through to x

let field: i32* = &pt.y;   // address of a field
let elem: i32* = &arr[1];  // address of an element
```

The `*` operator dereferences a pointer: `*p` is `p[0]` by another spelling, and can be read or assigned to the same way. Prefixes stack, so `**pp` peels a pointer to a pointer.

```
let x: i32 = 1;
let p: i32* = &x;

let v: i32 = *p; // read
*p = 5;          // write
*p += 5;         // compound write
```

The `->` operator reaches through a pointer to a struct: `p->field` is `(*p).field`, C-style, for fields and methods alike.

```
let p: Point* = &pt;
p->x = 5;        // (*p).x = 5
```

##### Null

`null` is the pointer literal. It works as an `opaque*`, adapting to whatever pointer type its context expects: initializing, comparing, passing, and returning any `T*`.

```
let p: i32* = null;

if (p == null) {
    // not pointing anywhere yet
}

if (p != null) {
    // safe to index
}
```

Without a context to adapt to, a bare `null` stays an `opaque*`:

```
let q = null; // q: opaque*
```

`null` only lands in pointer slots; giving it to a non-pointer is a compile-time error.

#### Arrays

Arrays are collections of same-type values. They are represented by `X[]` and their internal representation is always `{X*, u64}`, where `X*` is a pointer to `X` and `u64` is the number of elements. These are exposed as the members `data` and `length`, accessed like a struct's:

```
let arr: i32[];

let ptr: i32* = arr.data;   // the backing pointer
let n: u64 = arr.length;    // the element count
```

Declaring an array with a size `X[N]` backs it with `N` automatically allocated stack elements: its data points at them and its length starts at `N`. Since the contents come from the size, a sized declaration takes no initializer. The size is a constant integer expression: a literal, a `@const`, or any combination, evaluated at compile time and required to be positive.

```
@const HEADER = 16;

let buf: u8[64];              // buf.data -> 64 stack bytes, buf.length == 64
let body: u8[64 - HEADER];    // sized by a constant expression
```

An array can be indexed directly, reading or writing the `i`th element through its backing data:

```
let first: i32 = arr[0]; // equivalent to arr.data[0]
arr[1] = 5;
```

Arrays can be initialiazed with elements `a`, `b`, etc. enclosed by `[]` and separated by commas, a trailing one after the last element allowed.

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

#### Raw arrays

`@raw<T>[N]` is C's `T[N]`: exactly N elements of inline storage, no pointer and no runtime length. Where an `X[]` is a `{pointer, length}` pair over backing data, a raw array *is* its data, which is what C ABIs expect of fixed-size array fields:

```
struct buf {
    len: i32;
    data: @raw<u8>[16]; // 16 bytes inline, C layout
}
```

`N` is any constant integer expression: literals, `@const` names, `sizeof`, or any mix. The size is part of the type, so `@raw<i32>[4]` and `@raw<i32>[8]` never convert into each other.

```
@const N = 4;

let a: @raw<u8>[N * 2 + sizeof(i32)];
a[0] = 1;              // elements index in place, unchecked like C's
a.length;              // the element count, a compile-time constant
let p: u8* = &a[0];    // a plain pointer into the storage
let q: @raw<u8>[12]* = &a; // or to the whole array
```

A raw array is a value: assignments and calls copy all N elements. There is no implicit decay; pass `&a[0]` where a `T*` is wanted.

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

References to a type `T` are represented by `&T`. References cannot be dereferenced, meaning that you can't obtain the address where the value is stored through the `&` operator. This covers anything reached through the reference: for `s: &S`, both `&s` and `&s.member` are compile errors, since either would leak the caller's storage.

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

A function may also return a reference, `-> &T`, provided it has a reference parameter to derive it from, the receiver usually; returning storage that dies with the call (a local, a parameter's copy) has no reference to give. The `return` takes the value's address, and the call's result reads as the T it aliases, like a reference parameter does: reading copies the value out, while calling a [method](#methods) on it, or returning it along, keeps aliasing the original.

```
fn List<T>::get(self: &List<T>, index: u64) -> &T {
    return self.data[index];
}

list.get(i).push(x);     // acts on the element inside the list
let copy = list.get(i);  // copies the element out
list.get(i) = 9;         // assigns through the reference
list.get(i) += 1;        // compound assignment too
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

Type aliases give an existing type expression a new name. They're declared through `@type` followed by their name, `=`, and the aliased type expression, ending in `;`:

```
@type <name> = <type expr>;
```

```
@type string = char[];
@type fnc1 = fn();
@type fnc2 = fn() -> bool;
@type fnc3 = fn(char[]);
```

#### Generic type aliases

Type aliases are generic when their name is followed by an arbitrary number of placeholder types `A`, `B`, etc. enclosed by `<>` and separated by commas.

```
@type cmp<T> = fn(T, T) -> bool;
@type fnc<T, U> = fn(T, U);
@type fnc<T, U> = fn(T) -> U;
```

A concrete spelling supplies the arguments wherever a type is written: `cmp<i32>` is `fn(i32, i32) -> bool`. The target may be any type over the parameters, including a [generic struct](#generic-structs) or another generic alias (`@type boxes<T> = List<Box<T>>;`); the same modifier rule applies to arguments, and cycles are reported like any alias cycle.

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

#### Sizeof

`sizeof` yields the size in bytes of a type, or of a variable's declared type, computed at compile time. It takes either between its parentheses:

```
sizeof(T)
sizeof(v)
```

```
let c: char = 'a';
sizeof(char);   // 1
sizeof(c);      // 1

let msg: char[] = "hello";
sizeof(char[]); // 16: an array is a {pointer, length} pair
sizeof(msg);    // 16
```

The result adopts the integer type of its context like a literal does, defaulting to `u64`. Structs measure their full layout, padding included, so `@packed` and `@align(N)` change what `sizeof` reports.

Being a compile-time constant, `sizeof` also works anywhere one is required: `@const` values, enum member values, and array sizes.

```
@const WORD = sizeof(u64);

let buffer: u8[sizeof(i32) * 8];
```

### Enums

Enums are collections of constants. They are declared through the keyword `enum` followed by their name. Their members are declared by name, separated by commas.

```
enum name {
    ABC,
    DEF,
}
```

Members are accessed through the enum's name and `::`:

```
let color: name = name::ABC;
```

Optionally, you can define a specific value for any of their members through `= <value>` after their name. The value is a constant integer expression, and may combine literals, `@const` constants, and members already declared:

```
enum name {
    ABC,
    DEF = 5,
    GHI = name::DEF | 0x10,
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

A struct value is built with an aggregate literal: positionally, filling every field in order, or by name through `field = <expr>`, in any order. A named literal may fill any subset of the fields; the rest start at zero. A trailing comma after the last field is allowed in both forms.

```
let a: S = {1, 2};          // positional: every field, in order
let b: S = {b = 2, a = 1};  // named: any order
let c: S = {a = 1};         // named: b starts at zero
```

A struct's layout and access can be directed with decorators, stacking in any order. `@packed` drops the padding between fields, C's `__attribute__((packed))`.

```
@packed struct Header {
    tag: u8;
    size: u32;   // at offset 1, no padding
}
```

`@align(N)` aligns every allocation of the struct (locals, parameters, and globals) to N bytes, which must be a power of two.

```
@align(64) struct CacheLine {
    hot: i64;    // allocations start on a cache line
}
```

`@volatile` makes every access to the struct's values a volatile one, which the optimizer may neither elide nor reorder. The property lives on the type, so unlike C there is no way to hold a non-volatile value of it.

```
@volatile struct Reg {
    status: u32; // every read and write really happens
}
```

Structs can be forward-declared: declared with no body at all. This is mainly useful for opaque structs, whose fields are never given and which are only ever handled through a pointer.

```
struct Handle; // forward declaration, never given a body

fn open() -> Handle*;
fn close(h: Handle*);
```

#### Field defaults

A field may declare a default value after its type, taken wherever the field is left unfilled:

```
struct List<T> {
    data: T* = null;
    length: u64;
    capacity: u64 = 8;
}
```

A bare declaration of a struct with any default starts from its defaults, the undefaulted fields zeroed (`let l: List<i32>;` holds `{null, 0, 8}`), and defaults of nested struct fields cascade. A named aggregate literal fills what it names and defaults the rest (`{ length = 2 }` keeps `data = null`); a positional literal still fills every field. A struct with no defaults anywhere stays uninitialized on a bare declaration, as ever.

Defaults are written in the struct's declaration, so they see no local names: literals, `null`, constants, enum members, and `sizeof` are the natural fits. Union fields take no default, since their fields share one storage, and module-level globals keep their zero initialization.

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

Each argument list stamps out one concrete struct at compile time, shared by every use spelling the same arguments; arguments may be any concrete type, including other instantiations (`Box<Box<i32>>`), and a field may name its own instantiation through a pointer (`next: Node<T>*`). A modifier-carrying argument (`const T`, `&T`) is rejected: substituted into a derived position like `T*`, the modifier would silently move where it applies.

### Unions

Unions are declared like structs through the `union` keyword, but their fields all share one storage: writing one field and reading another reinterprets the same bytes, C-style.

```
union <name> {
    a: T;
    b: U;
}
```

```
union pun {
    f: f64;
    bits: u64;
}

let u: pun;
u.f = 1.0;
u.bits; // 1.0's raw IEEE bits
```

A union's size and alignment are its largest field's, inside enclosing structs too. Since the fields overlap, a union takes no aggregate literal: initialize it by assigning one of its fields. `@align(N)` and `@volatile` apply like a struct's; `@packed` has no field layout to act on and is refused.

#### Unnamed structs and unions

`struct { ... }` and `union { ... }` also work directly as types, wherever a type is written, C-style. The usual home is a field, the tagged-value pattern:

```
struct datum {
    type: i32;
    u: union {
        s: char*;
        b: bool;
        i: i64;
        f: f64;
    };
}

d.u.i = 42; // fields chain through like any other
```

An unnamed type's identity is structural: two spellings with the same fields are one type, so a `struct { x: i32; y: i32; }` local passes to a `struct { x: i32; y: i32; }` parameter directly. They compose everywhere a named type would: locals, aliases, raw arrays, pointers, `sizeof`, and each other.

An unnamed struct or union can also be a member with no name of its own: its fields then hoist into the enclosing struct, C-style, nesting included:

```
struct Result {
    ok: bool;
    union {
        value: i64;
        error: u8;
    };
}

r.value = 42; // reaches the unnamed union's field directly
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

`&self` is sugar for exactly that, spelling the receiver's type for you, `&S<A, B>` included for a [generic struct's](#methods-of-a-generic-struct) methods:

```
fn S::method(&self) {
    // ...
}
```

If the method does not mutate the instance, the receiver should be declared `self: const &S` (or `const &self`) instead, so it can also be called on a `const S`. Calling a mutating method (`self: &S`) on a `const` instance is an error.

```
fn S::read(const &self) -> T {
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

The receiver may be any expression, not just a name: a field chain, an indexed element, or another call's result: `self.items.get(i).init(n)` chains through a [reference return](#references).

#### Constructors

For a struct `S` with an `init` method, calling `S(args)` builds an instance in place: stack space, the struct's [field defaults](#field-defaults), then `S::init(self, args...)`. It is the expression form of:

```
let s: S;
s.init(args...);
```

Being an expression, it works anywhere a value does (bound, passed, or chained):

```
let lst = List<String>();   // a generic struct spells its arguments
lst.push(String());         // constructed in argument position
Counter(0).bump();          // methods chain on the temporary
```

A struct without an `init` method has no constructor to call, and a generic struct constructs only with its type arguments spelled (`List<...>()`).

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

#### Static methods

A method whose first parameter is not its receiver is a static method: it belongs to the type, and no instance joins its arguments.

```
fn List<T>::from_array(arr: const T[]) -> List<T> {
    let lst = List<T>(arr.length);
    lst.append(arr);
    return lst;
}
```

It is called through the type (`S::method(args...)`, with a generic struct spelling its arguments: `S<A, B>::method(args...)`) or through an instance, which passes nothing extra either way:

```
let lst = List<i32>::from_array(arr);
let cpy = lst.from_array(other);
```

A [type alias](#type-aliases) reaches them like the type it names. Since `S(args)` passes the instance as `init`'s receiver, a static `init` leaves the type without a [constructor](#constructors).

#### Method references

A bare `S::method` (or `S<A, B>::method` for a generic struct) is a [function reference](#function-references) value. An instance method's reference takes the receiver as an ordinary `&S` first argument; a static's takes only its own.

```
let read = Counter::value;      // fn(const &Counter) -> i32
let dbl = Counter::twice;       // a static: fn(i32) -> i32

read(c);
apply(Counter::twice, 5);       // passed like any function reference
```

A method with its own generic parameters has no bare reference: there is no single function to refer to.

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

An interface can only type a parameter. There is no runtime dispatch: like [generic functions](#generic-functions), `f` compiles once per concrete argument type, and each call checks that its argument's type implements the interface. Each interface parameter is independent, so `fn both(a: Named, b: Named)` takes two different implementers. The body can use the interface's fields and actions, and a function cannot return an interface value: it returns the concrete type.

Their actions are declared the same way as struct methods, but with the interface as the receiver's type and no body, describing the signature a struct must implement:

```
fn Named::greet(self: &Named) -> char[];
```

Actions take the [`&self` sugar](#methods) like any method declaration.

Interface conformance is nominal: a struct only implements an interface when it says so, through `: I` after the struct's name. Since there's no inheritance, `:` in that position always introduces interfaces. Implementing one still requires declaring its fields (with the declared types) and providing its actions (with the declared signatures); each claim is checked once every declaration is in, and a generic struct's instances check with their arguments substituted, so `struct List<T>: Iterable<T>` makes each `List<i32>` implement `Iterable<i32>`:

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
```

#### The iteration interfaces

`Iterator<T>` and `Iterable<T>` are builtin, visible everywhere without an import:

```
interface Iterator<T>;

fn Iterator<T>::has_next(&self) -> bool;
fn Iterator<T>::next(&self) -> &T;

interface Iterable<T>;

fn Iterable<T>::iterator(&self) -> Iterator<T>;
```

A struct claiming `: Iterator<T>` provides both actions, `next` handing back a [reference](#references) to the element. A collection claims `: Iterable<T>` and provides `iterator`, whose declared `Iterator<T>` return is satisfied by any implementing type; that is the general rule when an action declares an interface return. Any `Iterator<T>` parameter then walks the elements:

```
fn sum(it: Iterator<i32>) -> i32 {
    let total = 0;
    while (it.has_next()) {
        total += it.next();
    }
    return total;
}
```

[Arrays](#arrays) come iterable: a `T[]` implements `Iterable<T>` through the builtin `ArrayIterator<T>`, so an array passes to an `Iterable<T>` parameter and answers `iterator()` directly:

```
struct ArrayIterator<T>: Iterator<T> {
    arr: T[];
    index: u64;
}

let it = nums.iterator();   // an ArrayIterator over the array
it.next() = 5;              // the references reach the array itself
```

### Error handling

Errors are handled through `Result<V, E>`, a builtin struct that contains a return value or an error; and `Result<E>`, a builtin struct that only contains an error value. Both are visible everywhere without an import, and the argument count picks between them.

```
struct Result<V, E> {
    ok: bool;
    union {
        value: V;
        error: E;
    };
}

struct Result<E> {
    ok: bool;
    error: E;
}
```

`ok` tags which member holds: in `Result<V, E>`, `value` and `error` share one storage through the [unnamed union](#unnamed-structs-and-unions), so the whole result costs one tag plus the larger of the two.

Results are built through the builtin `Ok` and `Error` functions, one pair per `Result` shape:

```
fn Ok<V, E>(v: V) -> Result<V, E>;   // ok = true, value = v
fn Ok<E>() -> Result<E>;             // ok = true
fn Error<V, E>(e: E) -> Result<V, E>; // ok = false, error = e
fn Error<E>(e: E) -> Result<E>;      // ok = false, error = e
```

Their type arguments usually come from the expected type (the declared return type, an annotated `let`, or a parameter), since the arguments alone cannot name both `V` and `E`; anywhere else they're spelled explicitly:

```
fn divide(a: i32, b: i32) -> Result<i32, MathError> {
    if (b == 0) {
        return Error(MathError::DIVISION_BY_ZERO);
    }
    return Ok(a / b);
}

let r = divide(10, 2);
if (r.ok) {
    // r.value holds the quotient; r.error is meaningless here
}

let e = Error<i32, MathError>(MathError::OVERFLOW); // no context: spelled out
```

## Concepts

### Scopes

They are the context where stuff happens.
