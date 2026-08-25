# sielang

Sie is a modern C-flavored language with minimal syntax, a strong type system and type inference. The main goal of this project is to simplify the coding experience for system programming by implementing modern features like defer statements and error handling via tagged unions, plus features from higher-level languages like generics, typed variadics, Any and foreach loops; all while still providing full low-level control of the hardware.

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
- `-c` compiles to an object file without linking, named after the source (`main.sie` → `main.o`) unless `-o` says otherwise. The object defines the named sources and their includes; an imported module joins as declarations only, its definitions coming from its own `-c` object at link. See [Imports](#imports).
- `-I <dir>` adds a directory to the include search path. The `lib/` directory next to each source file is always searched.
- `-O <n>` sets the optimization level, cc-style: `-O0` (the default) emits code as generated, and `-O1` through `-O3` run LLVM's standard optimization pipeline. It applies to every output form, including executables, objects, `--emit-llvm`, `--emit-asm`, and `--run`.
- `-g` emits DWARF debug info, cc-style: every instruction maps to its source line, and every function, parameter, and variable is described with its type. A `-g` build debugs at source level in lldb or gdb: breakpoints by file and line, stepping, `bt` with Sie lines, and `frame variable` showing struct fields, arrays as their `{data, length}` pair, and unions. Debug at `-O0`, where nothing is reordered; on macOS, keep the `.o` the build leaves next to the executable, since the debugger reads the DWARF from it.
- `-l <lib>` links against a library, passed through to the linker: `-l m` links the C math library. Under `--run`, the library is loaded into the isolated JIT worker instead, its symbols resolvable the same way.
- `-L <dir>` adds a directory to the library search path.
- `--target <triple>` compiles for a target triple instead of the host (`x86_64-unknown-linux-gnu`, say). It aims everything at the target: the object code, the [target constants](#target-constants), and every `@sizeof`. Cross-built objects are best taken out with `-c`, since linking still runs the host's `cc`; `--run` only accepts the host's own triple, as its isolated JIT worker executes native code for the current machine.
- `--emit-llvm` prints the LLVM IR and exits, without building.
- `--emit-asm` prints the target's assembly and exits, without building.
- `--run` JIT-compiles and runs the program in place of building it, exiting with the program's own exit code. Anything after the flag is passed along as its arguments:

```
siec main.sie --run arg1 arg2
```

### Compilation phases

Compilation is declaration-order independent: moving a type or extension before or after the code that uses it does not change meaning. The loader builds the unit, then the compiler runs four phases:

0. **Discover and select.** Recursively find imports and includes from the requested sources. A condition guarding an include uses only the [loader-safe constant environment](#conditional-compilation).
1. **Parse.** Every selected file has a syntax tree before types are asked about.
2. **Collect.** Gather definitions, types, callables, generics, and interface claims across the whole unit.
3. **Resolve.** Resolve aliases, fields, signatures, generic arguments, and bounds against that inventory.
4. **Check.** Check bodies, conformance, and assertions once resolution is done.

So an extension declared later, or imported from another module, still applies where its bound holds. Generic instances follow the same order on a worklist until nothing new remains; LLVM emission starts only after that fixed point, and cannot invent further instances while lowering.

### Editor support

`sie-lsp` is a language server on the compiler's front end. It recompiles open buffers as they change and serves diagnostics, outline, completion, signature help, hover, and go-to-definition from what the compiler knows. Inside a function, method, or macro call, signature help lists its parameters and follows the active one as commas are entered.

```
pip install -e '.[lsp]'
```

`editors/` holds the clients: `editors/vscode/sie` for VS Code, `editors/nvim` for Neovim, `editors/helix/languages.toml` for Helix, and `editors/tree-sitter-sie` for the grammar. Any LSP editor can run `sie-lsp` over stdio for `.sie` files. Include paths come from the nearest `package.toml` and the workspace root's, the way `sie build` resolves them; extras pass as `includePaths` in the initialization options.

## The package manager

`sie` is the project-level tool. Where `siec` compiles a list of sources, `sie` works from a package: a directory holding a `package.toml` manifest that names it and says what it is made of.

`[package]` says who a package is, and one of `[app]` or `[library]` says what it is. A **library** is installed, for other packages to build against:

```
[package]
name = "openssl"
version = "1.0.0"

[library]
sources = ["src/"]
libs = ["ssl", "crypto"]

[dependencies]
libc = "~1"
```

An **app** is built, into a binary that runs:

```
[package]
name = "helloworld"

[app]
sources = ["src/"]

[dependencies]
core = "*"
```

The two are exclusive, and one of them is required: a library has no entry point to build, an app is the end of the line and nothing builds against it, and a manifest declaring both says nothing about which it is. `sources` and `libs` belong to whichever it is, since they describe what the package is made of rather than who it is.

With no command `sie` takes the package to act on as its argument, a directory holding a manifest, defaulting to the working directory, and prints what that manifest says:

```
sie                 # the package here
sie packages/core   # the one in that directory
```

Naming a file instead of a directory reads that file as the manifest.

### Installing

`sie install` copies a **library** into the install root (`$SIE_PATH/lib`, or `~/.sie/lib` when unset), under `<name>@<version>` from its manifest. The path defaults to the working directory:

```
sie install packages/openssl   # the package in that directory
sie install                    # the package here
```

It copies the manifest, the `[library]`'s `sources`, and any `readme` or `license-files` the manifest names. An `[app]` cannot be installed: it is built, not installed. Installing again replaces what was there.

### Uninstalling

`sie uninstall <name>` removes a package when exactly one version of it is installed:

```
$ sie uninstall zlib
uninstalled zlib@1.0.0
```

When several versions are installed, a bare name removes nothing and lists the choices. Name the exact one with `<name>@<version>`, or pass `-a`/`--all` to remove every installed version:

```
sie uninstall zlib@1.0.0
sie uninstall --all zlib
```

An exact spec removes only that version. `--all` takes a bare package name and leaves other packages untouched.

`sie list` says what is in the install root, each package as its spec and what its manifest says it is:

```
$ sie list
core@1.0.0       Sie Standard Library
libc@1.0.0       Bindings for the C Standard Library
openssl@1.0.0    Bindings for OpenSSL
zlib@1.0.0       Bindings for zlib
```

Packages are ordered by name and then by version, versions comparing as numbers so `9.0.0` comes before `10.0.0`. The directory name is the identity, so a package whose manifest has gone missing is still listed, without a description: it is installed, and that is how anyone finds out. Nothing installed is not a failure, and the listing stays empty while the note goes to standard error.

### Building

`sie build` compiles an **app** against the libraries that are installed, writing the binary to `<path>/build/<name>`:

```
$ sie build examples/helloworld
building helloworld
  core@1.0.0
  libc@1.0.0
  mpdecimal@1.0.0
  posix@1.0.0
built examples/helloworld/build/helloworld
```

Pass `--run` to JIT-run the resolved package without writing a binary. As with
`siec --run`, everything after the flag is passed to the program and the command
returns the program's exit status:

```
$ sie build examples/helloworld --run arg1 arg2
running helloworld
  core@1.0.0
  libc@1.0.0
```

An app needs a `name` for the binary, but no `version`. Pointing `build` at a `[library]` is an error: it is installed, not built.

`[dependencies]` names packages and which versions will do (`*`, `~1`, `^1.2.3`, comparisons, or a bare version). They resolve from the install root, newest first when several fit, and their sources and `libs` join the build. Library search directories come from `LIBRARY_PATH`, as for any C build. `-O` and `-g` pass through to the compiler.

## The language

### Imports

`import` pulls in a module by its dotted path. Members are reached through that path:

```
import std.io;

fn main() -> i32 {
    std.io.println("hello world");
    return 0;
}
```

Specific members can be brought in unqualified with `{}` and `from`, and either a member or the module itself can be renamed with `as`:

```
import { f, g as h } from module.submodule;
import module.submodule as sub;
```

Every file is a module: `import a.b` loads `a/b.sie`. A file loads once no matter how often it is imported, so cycles are fine. A module exports its top-level declarations except `@static` and `@private` ones; imports are not re-exported.

#### Include

`@include("path")` splices a `.sie` file into the current one, like C's `#include`:

```
@include("libc/stdio")
```

The path is searched in the including file's directory, then `-I` directories, then `lib/` beside each source, the working directory, and `lib/` under it. There is no namespace: the file's declarations compile as part of this unit, so under `-c` a definition included twice collides at link. Prefer declarations in included files and definitions in modules. An include may sit inside an `@if` ([conditional includes](#conditional-compilation)).

### Variables

Variables are declared with `let`, optionally typed and initialized:

```
let v: T;
let v: T = <expr>;
let a = f();   // type inferred from the initializer
```

A bare `let v;` with neither type nor initializer is rejected. Values are updated with `=`:

```
v = <expr>;
```

#### Assignment and ownership

Initialization creates a new value and never invokes an assignment interface. Plain assignment updates an existing one, choosing its operation from how the right-hand side is owned:

```
a = b;       // borrows b
a = move b;  // consumes b; using b afterward is an error
a = make();  // consumes the unnamed temporary
```

`move` takes an owned local as a whole. After a move, the variable cannot be read until it is reassigned. Three builtin interfaces customize the store:

```
interface Clone {
    fn clone(const &self) -> Self;
}

interface AssignFrom<T> {
    fn assign_from(&self, source: const &T);
}

interface Assign<T> {
    fn assign(&self, source: T);
}
```

`a = b` calls `assign_from` when claimed, else clones when both sides share a type that implements `Clone`. `a = move b` and assignments from temporaries call `assign` when claimed. Without a matching claim, assignment keeps the ordinary store.

#### Destruction and RAII

Types that claim the builtin `Destroy` interface get deterministic cleanup:

```
interface Destroy {
    fn destroy(&self);
}
```

An initialized local or by-value parameter that implements `Destroy` is destroyed when it leaves scope, sharing the scope's `defer` stack in reverse order:

```
let first = Resource();
defer log();
let second = Resource();
// second.destroy(), log(), first.destroy()
```

Moving, returning, or passing the value by ownership transfers that cleanup. `destroy` is responsible for the whole value, including owned fields; the compiler does not destroy fields for you. `drop place;` and `value.destroy()` do the same for a mutable place and disarm automatic cleanup.

#### Raw storage slots

`Slot<T>` has `T`'s size and alignment but no automatic lifetime: you mark each transition yourself. It does not implement `Destroy`.

```
let slot: Slot<Resource>;

slot.write(Resource());     // uninitialized -> owned value
slot.write_from(value);     // copy, or clone if T: Destroy
inspect(slot.get());        // const &T
slot.get_mut().update();    // &T
slot.assign_to(target);     // borrow into an existing T
slot.replace(Resource());   // destroy old, write new
let value = slot.take();    // move out, leave uninitialized
slot.drop();                // destroy in place
```

- `write(value: T)`: move into an uninitialized slot.
- `write_from(value: const &T)`: copy into an uninitialized slot; owned `T` must also implement `Clone`.
- `get() -> const &T`: borrow an initialized value.
- `get_mut() -> &T`: mutably borrow an initialized value.
- `assign_to(target: &T)`: assign from the slot into an existing `T` (`AssignFrom` / `Clone`).
- `replace(value: T)`: destroy the current value and move a replacement in.
- `take() -> T`: move the value out and leave the slot uninitialized.
- `drop()`: destroy the value and leave the slot uninitialized.

`write` and `write_from` need an empty slot; the rest need a live value. There is no `unsafe`: the caller meets those preconditions. Standard containers (`List`, `Stack`, `Queue`, `Map`) track which slots are live so their users work with `T` directly.

Automatic destruction follows structured control flow only. Process exit, signals, foreign exceptions, and aborting `panic` do not unwind Sie scopes; globals and statics are not destroyed at exit.

### Constants

Constants are compile-time constant expressions declared through `@const`. Unlike a `let` variable, a constant has no storage of its own: it's substituted with its value at compile time, similar to a type-safe version of C's `#define`. They must be initialized and cannot be reassigned. The type annotation is optional, inferred from the value when omitted:

```
@const name: T = <value>;
@const name = <value>; // type inferred
```

Each module keeps its own constants: two modules may both declare a `SEEK_SET`, like stdio and unistd do. A use resolves to the nearest declaration its file's view reaches: the file's own (or an include's) first, then a member import's, then the compilation unit's. Two equally near declarations are ambiguous and rejected; the qualified spelling names one module's.

#### Target constants

The compiler defines constants for the compilation target, taken from the target triple (the host's, or the one `--target` names). `TARGET_OS`, `TARGET_ARCH`, and `TARGET_ENV` hold the current target's families; one constant names each family they can match:

| OS           | Architecture   | Environment   |
| ------------ | -------------- | ------------- |
| `OS_DARWIN`  | `ARCH_X86_64`  | `ENV_GNU`     |
| `OS_LINUX`   | `ARCH_AARCH64` | `ENV_MUSL`    |
| `OS_WINDOWS` | `ARCH_RISCV64` | `ENV_MSVC`    |
| `OS_NONE`    | `ARCH_UNKNOWN` | `ENV_ANDROID` |
| `OS_UNKNOWN` |                | `ENV_ELF`     |
|              |                | `ENV_UNKNOWN` |

`OS_NONE` marks bare-metal targets (a triple like `riscv64-unknown-none-elf`). `TARGET_ENV` is the optional fourth field of the triple (`gnu`, `musl`, `msvc`, `android`, `elf`, …); triples without one leave it `ENV_UNKNOWN`. The unknowns catch anything else the compiler doesn't classify.

```
case (TARGET_OS) {
    when OS_DARWIN:  setup_darwin();
    when OS_LINUX:   setup_linux();
    when OS_WINDOWS: setup_windows();
    else:            fail("unsupported platform");
}

@const PAGE_ALIGNED = TARGET_ARCH == ARCH_AARCH64;
@const USE_MUSL = TARGET_ENV == ENV_MUSL;
```

They behave like any other `@const` (usable in constant expressions, case arms, and array sizes), except that redeclaring one is an error.

### Macros

Macros are compile-time substitutions declared through `@macro`. One without a parameter list expands wherever its bare name appears; one with a list expands at each call:

```
@macro name = <expr>;                 // a bare `name` expands
@macro name(param1, param2) = <expr>; // `name(a, b)` expands
```

Either kind may hold a [block](#blocks) instead, producing the use's value through `emit`:

```
@macro name(param1, param2) {
    // ...
    emit <expr>; // optional
}
```

Substitution passes expressions, not values: an argument named twice evaluates twice, and assigning to a parameter writes through the caller's argument:

```
@macro swap(a, b) {
    let t = a;
    a = b;
    b = t;
}

swap(p.x, p.y); // writes through the members
```

The expansion is type-checked. Names in the body resolve where the macro was written; argument expressions resolve at the call.

### Conditional compilation

`@if` compiles a group of top-level declarations only when a compile-time condition holds, with an optional `@else`:

```
@if (<const expr>) {
    // ...
} @else {
    // ...
}
```

The condition is a constant expression: literals, `@const` names, enum members, `@sizeof`, arithmetic, comparisons, and `and`/`or`/`not`. The unchosen branch is skipped entirely, never parsed into the program, so its declarations may collide with the chosen one's:

Constant integer arithmetic follows runtime signed division and remainder:
division truncates toward zero, and a remainder has the dividend's sign.
`and` and `or` short-circuit here exactly as they do at runtime. Division by
zero, negative or out-of-range shifts, and results wider than every Sie integer
type are compile-time errors rather than Python or backend failures.

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

A branch may hold any top-level declaration (functions, structs, enums, globals, constants, type aliases) including further `@if` blocks, and a constant declared in a chosen branch is visible to the conditions after it.

Conditions that need no type meaning are selected before declarations are collected. A condition using an enum member, `@sizeof`, or `@typeid` waits until the active type inventory is resolved, so it may inspect a declaration written later. Once such a branch is chosen, its declarations are registered before its nested conditions run. A condition cannot depend on a declaration whose own existence it decides; that dependency has no inventory in which to resolve.

An `@include` may also sit in a branch: only the chosen arm's files load, and an unchosen arm's include is never resolved, so its file need not exist on this platform, C-header-style:

```
@if (TARGET_OS == OS_DARWIN) {
    @include("darwin/_errno");
} @else @if (TARGET_OS == OS_LINUX) {
    @include("linux/_errno");
}
```

Because includes decide what the program _is_, a condition guarding one evaluates while files are still loading, before the program assembles. Such a condition is held to what exists at that point: literals, operators, the target constants, and `@const` values already loaded (the file's own, its includes', and earlier chosen arms'). Enum members and `@sizeof` need the assembled program and cannot appear there; an `@if` with no include in reach keeps the [full constant language](#conditional-compilation). An `import` stays unconditional either way: to vary by platform, import one module that hides the choice behind a conditional include.

#### Error

`@error("message")` stops the compilation with the message it carries. Since an unchosen branch is never resolved, one inside an `@if` is reached only when that branch is the chosen one, which is how a set of platform arms refuses everything it has no binding for:

```
@if (TARGET_OS == OS_DARWIN) {
    // ...
} @else @if (TARGET_OS == OS_LINUX) {
    // ...
} @else {
    @error("Unsupported OS")
}
```

The message is reported like any compile error, naming the file and line it sits on. Outside an `@if` there is nothing to gate it, so the file simply cannot build; an `@error` in an imported module blames that module, not its importer. A trailing `;` is fine, statement-style.

#### Static assert

`@static_assert(cond, "message")` requires a compile-time condition to hold, C's `static_assert`: nothing happens when it does, and the message stops the compilation when it doesn't, reported as `static assertion failed: <message>`.

```
struct Header { a: u64; b: u64; }

@static_assert(@sizeof(Header) == 16, "Header must stay two words");
```

Unlike an `@if`, an assert declares nothing, so it is checked once the whole program is registered rather than while the conditions are still choosing what to compile. Its condition can therefore weigh what those declarations turned out to be: a struct's `@sizeof`, an enum's members, and constants, whatever order they were written in. An assert inside an `@if` still follows its branch, checked only when that branch is the chosen one.

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

### Truthiness

Values other than `bool` can still be used wherever a truthy value is expected:

- Numbers and `char`s are truthy when they're `!= 0`.
- Booleans are truthy when they're `true`.
- Pointers are truthy when they're non-null.
- Arrays are truthy when their length is `> 0`.

These built-in rules implement the `Truthy` interface directly in the compiler. A struct can implement the same interface through a read-only `truthy` method:

```
interface Truthy {
    fn truthy(const &self) -> bool;
}

struct Result<V, E>: Truthy { ... }

fn Result<V, E>::truthy(const &self) -> bool {
    return self.ok;
}
```

`Truthy` is contextual: it applies to `if`, loops, ternary conditions, and `and`, `or`, and `not`. It does not make a value implicitly convertible to `bool` in an assignment or argument.

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

`while` runs a body while its condition is truthy:

```
while (<expr>) {
    // ...
}
```

`for` drives a loop through an init, a condition, and a step:

```
for (let i: i32 = 0; i < n; i += 1) {
    // ...
}
```

Either loop's body may drop the braces when it is a single statement.

#### Foreach

`foreach (v : iterable)` walks a collection's elements. `v` is a [reference](#references) into the collection, so assigning it writes the element in place:

```
foreach (v : nums) {
    total += v;
    v = v * 2;
}
```

Anything `Iterable<T>` works ([arrays included](#the-iteration-interfaces)). `break` and `continue` steer the loop like any other.

#### Enumerate

The builtin `enumerate(x)` wraps an Iterable (or an iterator) in an iterator of `{index: u64, value: T}` pairs, counting from zero:

```
foreach (e : enumerate(nums)) {
    printf("%llu: %d\n", e.index, e.value);
}
```

`value` is a copy of the element, not a reference into the collection. A mutable iterator produces `Enumerated<T>` pairs through `EnumerateIterator<I, T>`; a const iterator produces `ConstEnumerated<T>` pairs through `ConstEnumerateIterator<I, T>`, whose `value` remains `const T`. A declared function named `enumerate` takes precedence over the builtin.

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

where `A`, `B` and `C` are concrete types. A by-value `Tuple` parameter may also use a parenthesized pattern (`(a, b): Tuple<A, B>`); see [Tuples](#tuples).

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

Entry-point types may use aliases and may carry an outer `const`; they are
checked after aliases resolve, including aliases imported from another module.
The entry must remain one public external definition, so `main` cannot be
`@extern`, `@inline`, `@static`, `@private`, `@override`, `@remove`, or
`@symbol`. A matching forward declaration may still precede its definition.

#### Const parameters

A parameter marked `const` is a promise not to mutate it. `a: T` and `a: const T` are represented identically; the latter simply cannot be reassigned or mutated through:

```
fn f(a: const A) {
    // a cannot be reassigned or mutated through here
}
```

A `T` passes where a `const T` is expected; a `const` pointer or array never passes where a mutable one is. Methods use the same spelling on their receiver: `self: &S` mutates, `self: const &S` only reads.

#### Default arguments

A parameter can declare a default with `= expr`, so a call may omit it:

```
fn greet(name: const char*, times: i32 = 1) {
    // ...
}

greet("sie");       // times is 1
greet("sie", 3);
```

Only trailing parameters may carry defaults. The expression is evaluated at each call, resolved where it was declared. Methods and [constructors](#constructors) take defaults the same way.

#### Overloading

A function name can be declared multiple times with different parameter lists, differing in types or in count. Methods overload the same way:

```
fn Decimal::add(&self, d: const &Decimal) -> Decimal { ... }
fn Decimal::add(&self, n: i64) -> Decimal { ... }
fn Decimal::add(&self, f: f64) -> Decimal { ... }
```

Each call ranks every matching overload together, including generic and interface-bound ones. Exact matches come first; a concrete overload wins when it is just as exact as a generic one. Only when there is no exact match does the call consider implicit conversions such as widening, array decay, `opaque*`, and `null`. No reachable candidate, or a tie at the winning rank, is a compile-time error.

An interface match keeps the argument's type, so it wins over a concrete overload that would have to convert it:

```
fn kind(value: i64) -> i32 { return 1; }
fn kind<T: SignedInteger>(value: T) -> i32 { return 2; }

let value: i32 = 0;
kind(value); // the SignedInteger overload; value stays an i32
```

An untyped integer literal counts as its first fitting signed type: `i32`,
`i64`, then `i128`. From there it converts like any other value:

```
dec.add(5);            // an i32, widened into the i64 overload
dec.add(5000000000);   // doesn't fit an i32: exactly the i64 overload
dec.add(other);        // a Decimal: exactly 'const &Decimal'
```

Signed and unsigned never mix: a `u8` argument widens into a `u64` candidate, never an `i64` one. A reference parameter (`&T`) needs its exact type, since it aliases the argument in place.

The return type is not part of the signature, so two overloads differing only there conflict. `@extern`, `@symbol`, and `main` functions cannot overload: each names one fixed symbol. A bare reference to an overloaded name (`let g = f;`) is an error, since without arguments there is nothing to pick by.

A function's module symbol carries its parameter types: `pick(i64)`, `List<char>::init(&List<char>,u64)`. Separately compiled units therefore name every signature alike, whatever their declaration order; only `@extern`, `@symbol`, and `main` keep their unmangled C symbols.

#### Overrides

`@override` deliberately replaces one matching function or method implementation. The target must already exist in the compilation unit with the same parameter and return types; without the decorator, defining that same function twice remains an error. Collection happens before override selection, so the declarations may appear in either order:

```
fn answer() -> i32 { return 1; }

@override
fn answer() -> i32 { return 42; }
```

A concrete receiver, such as `char[]` or `Box<i32>`, overrides a receiver family only for that concrete type and signature. Other instantiations and overloads keep the family implementation:

```
fn T[]::f(const &self) -> i32 { return 1; }

@override
fn char[]::f(const &self) -> i32 { return 42; }
```

A bounded template is a conditional override. It wins where its bound holds and falls back to the less-specific family everywhere else:

```
fn T[]::f(const &self) -> i32 { return 1; }

@where<T: Formattable>
@override
fn T[]::f(const &self) -> i32 { return 42; }
```

The same rule applies to bounded generic functions. Exact concrete overrides are more specific than bounded ones, and bounded overrides are more specific than an unbounded declaration. Two equally specific overrides that both apply are ambiguous and rejected; an override with no matching target is also an error.

#### Operator overloading

Binary operators on a struct operand are shorthand for method calls: `a + b` is `a.add(b)`, picking among `add`'s overloads by `b`'s type. The operators map to `add`, `sub`, `mul`, `div`, and `rem`.

```
let sum = dec + other;   // dec.add(other)
let scaled = dec * 10;   // dec.mul(10): the i64 overload
```

Compound assignment has methods of its own, `add_assign` through `rem_assign`, taking the value and returning nothing: `a += b` is `a.add_assign(b)`, which updates `a` where it stands. This matters when the binary operator builds a new value: `add` returning a fresh `Decimal` would leave `a += b` assigning that result back over `a`, dropping whatever `a` held. The in-place method spends no copy and leaves nothing behind.

```
dec += 1;                // dec.add_assign(1): dec updates in place
```

A type without the in-place method falls back to the operator's result, `a = a + b`, which is how numbers and simple structs work. So `add_assign` is the one to write when the value owns something.

Equality desugars the same way: `a == b` on a struct operand is `a.eq(b)`, and `a != b` is its negation, `not a.eq(b)`. There is no `ne` method to write. The four ordering operators share one method: each compares `cmp`'s sign against zero, `a < b` as `a.cmp(b) < 0`, C's `strcmp`-style.

```
if (dec == other) { ... }   // dec.eq(other)
if (dec != 1) { ... }       // not dec.eq(1): the i64 overload
if (dec < other) { ... }    // dec.cmp(other) < 0
```

The prelude declares an interface per operator: `Add<S, T>` requires `add(&self, value: T) -> S`, and `Sub`, `Mul`, `Div`, and `Rem` follow the same shape; `AddAssign<T>` requires `add_assign(&self, value: T)`, with `SubAssign`, `MulAssign`, `DivAssign`, and `RemAssign` alongside; `Eq<T>` requires `eq(&self, value: T) -> bool`, and `Ord<T>` requires `cmp(&self, value: T) -> i32`. Claiming one declares and enforces the contract, one claim per supported right-hand type:

```
struct Decimal : Add<Decimal, Decimal>, Add<Decimal, i64>, AddAssign<i64>,
                 Eq<Decimal>, Ord<Decimal> {
    // ...
}
```

The shorthand itself is structural, like `foreach` and `iterator()`: any struct with the method takes the operator, claimed or not. The interfaces are there to declare the contract and to bound generics.

Numeric primitives implement these interfaces intrinsically so `1.add(2)` is equivalent to `1 + 2`, and `n.add_assign(2)` to `n += 2`. A narrower method argument widens to the receiver's type as usual.

#### Indexed operators

Indexing has the same shorthand for structs. `a[key]` is `a.get_item(key)`, while `a[key] = value` is `a.set_item(key, value)`. The key and value types come from the selected overload, and a compound assignment reads, applies the binary operator, then writes the result back: `a[key] += value` is `a.set_item(key, a.get_item(key) + value)`.

```
struct Table : GetItem<u64, i32>, SetItem<u64, i32> {
    values: i32[];
}

fn Table::get_item(const &self, key: u64) -> i32 {
    return self.values[key];
}

fn Table::set_item(&self, key: u64, value: i32) {
    self.values[key] = value;
}

let table: Table = { [10, 20, 30] };
let first = table[0];  // table.get_item(0)
table[1] = 40;         // table.set_item(1, 40)
table[2] += 12;        // get_item, '+', then set_item
```

The prelude's `GetItem<K, V>` requires `get_item(const &self, key: K) -> V`; `SetItem<K, V>` requires `set_item(&self, key: K, value: V)`. A type may claim only the read capability, or both when it also writes. As with the other operator interfaces, the shorthand is structural, while a claim enforces the method's signature and lets the capability bound an interface parameter.

Native arrays, raw arrays, pointers, and tuples keep their built-in storage indexing: their `[]` never routes through these methods.

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

A type parameter is lexical: inside its function, method, receiver family, struct, interface, or alias, it wins over a same-named type or interface declared outside the template. The rule follows the placeholder through derived and nested forms such as `T[]`, `Box<T>`, and `fn(T) -> T`; outside the template, the global declaration keeps its ordinary meaning.

A parameter may carry a bound after `:`. An interface bound accepts any type that implements it; any other type-like bound (an intrinsic, alias, or struct) accepts that canonical type exactly. Bounds may refer to the other parameters, and apply whether the call infers its arguments or spells them:

```
fn hash<K: Hashable>(key: K) -> u64;
fn size<T, U: Iterable<T>>(values: U) -> u64;
fn word<T: u64>(value: T) -> T;
fn ordered<T: Hashable & Comparable<T>>(value: T) -> T;
```

`&` forms an explicit intersection: the argument must satisfy every listed type-like bound. Intersections work wherever bounds do, including generic functions, structs, aliases, interfaces, receiver methods, extensions, and `@where` environments. They are unordered (`I1 & I2` and `I2 & I1` describe the same bound), and each additional member makes an otherwise matching overload or override more specific.

An alias in a bound means its target, so `@type Word = u64; fn word<T: Word>(...)` has the same bound as the third declaration. A concrete interface claim can also fill parameters named only inside the bound: an argument implementing `Iterable<char>` binds `T` to `char` in `U: Iterable<T>`.

`Scalar` is a sealed builtin interface for the primitive value types: the signed and unsigned integers, `f32`, `f64`, `bool`, and `char`. It is useful when an implementation needs the builtin scalar representation while accepting every width. Structs, enums, pointers, and arrays do not satisfy it, and user declarations cannot claim it.

`Integer` is the same kind of sealed marker for every integer primitive (`i8`…`i128` and `u8`…`u128`). `SignedInteger` and `UnsignedInteger` narrow that further to one signedness each. Like `Scalar`, only the compiler's primitives satisfy them, and user declarations cannot claim them.

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

#### Variadic functions

A last parameter spelled `name...` is sugar for `name: const Any[]`: each extra call argument wraps as an [Any](#any) and packs into a borrowed array view, an empty one when none are given. The callee can inspect and forward that pack but cannot mutate it.

```
fn println(str: const char[], args...) {
    // args: const Any[]; args.length counts the extras
}

println("hello world");        // args.length = 0
println("hello {}", "world");  // args.length = 1
```

The body dispatches on each element with [`@typeof`](#any), and passing an `Any[]` or `const Any[]` itself forwards it as-is instead of re-packing, so variadics delegate to one another. Methods take the sugar too. `@extern` functions keep C's bare `...`, which passes arguments the C way instead.

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

Decorators stack, so `@static @inline fn` is both, except for `@extern`, whose function has no body for the others to act on; only `@noreturn`, which describes the signature rather than the body, rides along with it.

`@static let` declares a file-local global variable the same way: one storage location shared by every call, visible only to its own file. Its initializer, when given, must be a compile-time constant. As with a local `let`, an initializer can supply an omitted type; without an initializer the type remains required and the storage starts at zero, C-style. An `@extern let` always keeps its explicit ABI type.

```
@static let count: i32 = 0;
@static let ready = false;

fn bump() -> i32 {
    count += 1;
    return count;
}
```

#### Private

`@private` keeps a declaration out of its module's import surface without
changing its symbol or textual visibility. The defining file and files joined
to it through `@include` may still use it. Qualified imports and member imports
cannot.

```
@private @const DEFAULT = 0;
@private fn helper();
@private fn Parser::advance(&self);
@private struct State;
```

This differs from `@static`: a static function has file-local linkage and is
visible only in its own file, while a private declaration remains shared across
a textual include module.

#### Noreturn

Functions can be decorated with `@noreturn` to declare that they never give control back to their caller: they exit the process, loop forever, or hand off to another `@noreturn` function.

```
@extern @noreturn fn exit(code: i32);

@noreturn fn die(code: i32) {
    exit(code);
}
```

A statement calling an `@noreturn` function ends its path, so it satisfies a required return the same way a `return` would, in a whole function or a single branch:

```
fn checked(x: i32) -> i32 {
    if (x < 0) {
        die(1);       // this branch needs no return
    }
    return x;
}
```

Since it hands nothing back, an `@noreturn` function cannot declare a return type, and a `return` inside its body is a compile-time error. The promise passes to LLVM, which optimizes on it.

#### Deprecated

`@deprecated` marks a function or method as deprecated. Using it produces a warning without failing the build. An optional string adds advice to the warning:

```
@deprecated fn old_func() { }

@deprecated("use new_func")
fn older_func() { }

fn main() {
    old_func();   // warning: 'old_func' is deprecated
    older_func(); // warning: 'older_func' is deprecated: use new_func
}
```

Programs warn only about uses reachable from `main`. A library unit without a `main` warns about every use. Function [references](#function-references) count as uses, while uses inside another deprecated function stay quiet. Generic functions and methods can be deprecated the same way, and the decorator combines with others such as `@extern`.

#### Remove

Once a deprecated function is actually gone, `@remove("advice")` leaves a tombstone in its place: the declaration stands so its uses still name it, but there is nothing left to define, so it takes no body. Any use is a compile error quoting the advice:

```
fn new_func() { }

@remove("use new_func")
fn old_func();

fn main() {
    old_func(); // error: 'old_func' was removed: use new_func
}
```

Unlike a deprecation, a removal is not gated by reachability: the code cannot compile at all, so a use anywhere fails, references included. A tombstone nothing uses compiles fine, which is the point of leaving it: callers get the advice instead of `undefined function`.

Methods, generic functions, and a generic struct's or an array's methods all remove the same way. A removed generic never registers a template, so nothing can stamp it, and the name answers for the advice on its own:

```
@remove("use scale2") fn scale<T>(v: T) -> T;
@remove("use foreach") fn T[]::walk(&self) -> u64;
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

- Signed integers: `i8`, `i16`, `i32`, `i64`, `i128`.
- Unsigned integers: `u8`, `u16`, `u32`, `u64`, `u128`.
- Floats: `f32`, `f64`.
- Booleans: `bool`.
- Characters: `char`.

Integer literals may also be written in hexadecimal with the `0x` prefix:

```
let mask: u32 = 0xFF00;
```

One integer token may contain at most 4096 digits. This parser boundary keeps
malformed or generated source from exhausting the host integer converter.

Without a type context, an integer literal defaults to the first signed type
it fits: `i32`, then `i64`, then `i128`.

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

Comparisons are the exception: an `opaque*` can be compared with any pointer
type, on either side and with any comparison operator. Two typed pointers must
still have the same type; for example, comparing a `u8*` with an unrelated
`S*` is rejected.

An explicit `as` also reinterprets any typed pointer as any other, C-style: `text.data as u8*` reads a `char*`'s bytes. Only the spelling converts; a `const` contract stays put, so casting one away is rejected.

Integers and pointers cast into each other the same way, an address being a number either direction. This is how a binding spells a sentinel address, C's `(void *) -1`:

```
@const MAP_FAILED = -1 as opaque*;

let address = p as u64;     // and back, for alignment arithmetic
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

A sized struct field owns the same fixed backing inline in its containing
struct, so moving the struct cannot leave an internal pointer behind. Reading
the field still produces the ordinary `X[]` view, with `data` pointing at that
inline backing and `length` equal to `N`; indexing reads or writes the backing
directly.

```
struct State {
    words: u64[4];
}
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

An array literal is the `T[]` its elements infer, so the annotation may be dropped: `let arr = [1, 2, 3];` declares an `i32[]`. Only an explicit pointer context takes it as a bare pointer instead, the literal decaying to its data: `let ptr: i32* = [1, 2, 3];`. An empty literal has no element to infer from and needs its annotation.

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

`@raw<T>[N]` is C's `T[N]`: exactly N elements of inline storage, no pointer and no runtime length. Where an `X[]` is a `{pointer, length}` pair over backing data, a raw array _is_ its data, which is what C ABIs expect of fixed-size array fields:

```
struct buf {
    len: i32;
    data: @raw<u8>[16]; // 16 bytes inline, C layout
}
```

`N` is any constant integer expression: literals, `@const` names, `@sizeof`, or any mix. The size is part of the type, so `@raw<i32>[4]` and `@raw<i32>[8]` never convert into each other.

```
@const N = 4;

let a: @raw<u8>[N * 2 + @sizeof(i32)];
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
let inferred = "Hello";      // a char[] too
```

A literal is a `char[]` everywhere: it carries its length, indexes, takes the [builtin type methods](#builtin-type-methods), and the operator shorthands follow (`"a" + s`, `s == "a"`). Only an explicit `char*` context takes the bare pointer instead, C-style: `let p: char* = "Hello";`, or an `@extern` function's `char*` parameter.

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

A reference parameter normally aliases assignable storage in the caller. A `const &T` parameter only reads, so a literal or implicitly convertible value may pass too: it converts when necessary, materializes at the parameter's type, and is referenced in place. A mutable `&T` still needs caller storage of exactly that type, since conversion would make a temporary and writes to it would vanish.

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

#### Closures and nested functions

An anonymous arrow expression is a closure: it may use variables from the surrounding lexical scope without adding them to its declared parameter list. A named function declared inside another function behaves the same way. Parameters are typed inside the parentheses, and a return type goes before the arrow: `(value: i32) -> i32 => { return value; }`.

The body may be a block or a single expression. A block holds an ordinary statement list; the expression form is its compact one-statement counterpart:

```
let f = () => { ... };
let f = () => ...;
```

With an explicit return type, the expression is returned directly: `let answer = () -> i32 => 42;`. Without one, it is evaluated as a statement, which is useful for short callbacks such as `let increment = () => value += 1;`.

```
fn main() -> i32 {
    let value = 40;

    fn add_two() -> i32 {
        return value + 2;
    }

    let increment = () => { value += 1; };
    increment();
    return add_two() + value - 41; // 42
}
```

A raw `fn(...)` is one ABI function pointer and cannot carry captures. A closure instead has the explicit type `closure fn(...)`, represented by erased invocation code and an environment pointer. Parameters which retain or invoke a capturing function therefore say so:

```
fn invoke(callback: closure fn()) {
    callback();
}

let value = 42;
invoke(() => { use(value); });
```

`callback.env` exposes the opaque environment pointer for foreign callback APIs whose `user_data` parameter carries it. An explicit cast adapts the closure to the foreign signature: that signature must end in `opaque*`, its leading parameters must begin with the closure's declared parameters, and any parameters between them are ignored by the closure. A generic macro can package the cast while leaving the ABI visible at the call site:

```
@macro G_CALLBACK<ABI>(callback) = callback as ABI as GCallback;

fn Application::connect_activate(
    &self,
    callback: closure fn()
) {
    g_signal_connect(
        self.handle,
        "activate",
        G_CALLBACK<fn(GtkApplication*, opaque*)>(callback),
        callback.env
    );
}
```

Captured variables are promoted to stable shared storage when the closure is formed. The original scope and every closure capturing that variable therefore observe the same value, and `callback.env` remains valid when a foreign callback retains it beyond the creating function's return. Promoted closure storage is currently retained for the process lifetime. Future release support can let safe library wrappers reclaim it through a foreign API's destroy notification or an owned connection which disconnects before releasing its environment.

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

Aliases, enums, structs, generic structs, and interfaces share one type-name namespace: a name owned by one cannot be redeclared as another. A declaration in a chosen `@if` branch follows the same rule as a direct declaration, while an inactive branch owns no name.

#### Generic type aliases

Type aliases are generic when their name is followed by an arbitrary number of placeholder types `A`, `B`, etc. enclosed by `<>` and separated by commas.

```
@type cmp<T> = fn(T, T) -> bool;
@type fnc<T, U> = fn(T, U);
@type fnc<T, U> = fn(T) -> U;
```

A concrete spelling supplies the arguments wherever a type is written: `cmp<i32>` is `fn(i32, i32) -> bool`. The target may be any type over the parameters, including a [generic struct](#generic-structs) or another generic alias (`@type boxes<T> = List<Box<T>>;`); the same modifier rule applies to arguments, and cycles are reported like any alias cycle.

Alias parameters take the same [bounds as generic functions](#generic-functions), checked before the target expands:

```
@type Entry<K: Hashable, V> = Pair<K, V>;
```

#### Type casting

Any represented value can be explicitly viewed as another type through the
`as` keyword. Reading that view produces a value copy. Scalar representations
convert at their LLVM width even when the Sie type is not arithmetic, so
`char` and `bool` cast like their underlying integers. Addressable aggregate
values reinterpret the same storage through the target representation.

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

`@sizeof` yields the size in bytes of a type, or of a variable's declared type, computed at compile time. It takes either between its parentheses:

```
@sizeof(T)
@sizeof(v)
```

```
let c: char = 'a';
@sizeof(char);   // 1
@sizeof(c);      // 1

let msg: char[] = "hello";
@sizeof(char[]); // 16: an array is a {pointer, length} pair
@sizeof(msg);    // 16
```

The result adopts the integer type of its context like a literal does, defaulting to `u64`. Structs measure their full layout, padding included, so `@packed` and `@align(N)` change what `@sizeof` reports.

Being a compile-time constant, `@sizeof` also works anywhere one is required: `@const` values, enum member values, and array sizes.

```
@const WORD = @sizeof(u64);

let buffer: u8[@sizeof(i32) * 8];
```

#### Typename

`@typename` yields the canonical name of a type, or of a variable's declared type, as a `const char[]` baked in at compile time. Aliases expand, so the name is the one the compiler knows the type by:

```
let num: u64;
@typename(num);          // "u64"

let s: String;           // @type String = List<char>;
@typename(s);            // "List<char>"

let arr: i32[];
@typename(arr);          // "i32[]"

@typename(List<f64>);    // "List<f64>", a type spelling works directly
```

Inside a [generic](#generic-functions), the placeholder substitutes first, so `@typename(T)` names each instance's concrete type. Any expression works as the argument too, naming its static type without evaluating it: `@typename(args[i])`, `@typename(n + 1)`.

An [Any](#any) operand is the one runtime case: it answers with the wrapped type's name, looked up by its id in a table of every type the program wraps, an unknown id answering `"?"`. Under [separate compilation](#imports) each unit's table holds its own wraps, so an Any crossing `-c` units may answer `"?"` in a unit that never wraps its type.

#### Typeid

`@typeid` yields the 64-bit FNV-1a hash of the same canonical name, a compile-time `u64` identity for the type. Aliases share their target's id, distinct types get distinct ids, and generics substitute before hashing, so `@typeid(T)` identifies each instance's concrete type:

```
@typeid(u64);                        // the hash of "u64"
@typeid(s) == @typeid(List<char>);   // true for s: String

fn kind(id: u64) -> i32 {
    case (id) {
        when @typeid(u64): return 1;
        when @typeid(List<char>): return 2;
        else: return 0;
    }
}
```

Being a compile-time constant, it works anywhere one is required: `@const` values, enum members, case arms, and array sizes.

#### Any

`Any` is a builtin struct erasing a value's type behind its id:

```
struct Any {
    id: u64;      // the value's @typeid
    data: opaque*;
}
```

`v as Any` spills a copy of the value into the enclosing function's frame and pairs its address with its type's id. Since every `Any` is this one struct, an `Any[]` holds heterogeneous values, and a function over them is one function, never stamped per payload:

```
fn log(args: const Any[]) {
    foreach (arg : args) {
        case (@typeof(arg)) {
            when char[]: // ...
            when String: // ...
            else: // ...
        }
    }
}

log([1 as Any, "text" as Any, 2.5 as Any]);
```

The array and its `Any` entries are borrowed views. When an erased payload owns resources, inspect it through a const cast (`arg as const Resource`); that view receives no independent cleanup responsibility. Acquiring another owner remains explicit, for example by cloning that const view.

`@typeof(x)` yields the type id an expression carries: an `Any` operand reads its runtime `id` field, and any other operand folds to its static type's `@typeid` at compile time (the operand is never evaluated). Comparing it against a bare type name means the type's id, in `==`/`!=` and in `when` arms, where non-identifier spellings (`char[]`, `i32*`) work too:

```
@typeof(arg) == @typeid(u64);
@typeof(arg) == u64;             // the same, sugared
case (@typeof(arg)) {
    when u64: // ...
    when char[]: // ...
}
```

`a as T` reads the erased value back as `T`, unchecked: comparing `@typeof(a)` first is the caller's job. The pointed value lives in the frame of the function that wrapped it, so an `Any` outliving that frame dangles like any pointer to a local would; and wrapping erases the `const` contract, which the unwrapper chooses anew.

A `when` may also name an [interface](#interfaces): the arm is generic, expanding into one arm per type known to implement it, the body stamped with the concrete type wherever the interface is spelled. The cast in each stamped arm therefore reads the arm's own type:

```
case (@typeof(args[i])) {
when Formattable:
    let arg = args[i] as Formattable;  // 'as i64' in the i64 arm, ...
    result.append(arg.format(modifier));
}
```

The expansion covers every type claiming the interface, arrays included through the family's claim, so `when Iterable<char>:` arms `char[]` among the implementers. A type an earlier arm already matched never reaches its stamped arm: the first match still wins.

A nested interface argument expands per combination: `when Iterable<Formattable>:` substitutes each formattable type into the argument and arms every iterable of each, so an `i64[]` and a `P[]` both land in it when `i64` and `P` claim `Formattable`.

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

Optionally, you can define a specific value for any of their members through `= <value>` after their name. The value is a constant integer expression, and may combine literals, `@const` constants, and members of any enum:

```
enum name {
    ABC,
    DEF = 5,
    GHI = name::DEF | 0x10,
}
```

Every enum and member name is collected before those values resolve, so an expression may also reference a member or enum declared later. A cycle between member values is rejected with the chain that forms it.

Members are assigned values automatically, starting at 0 and increasing by 1 for each subsequent member. Setting a specific value for a member changes the counter for the following ones, which then keep increasing from there.

Both explicit values and automatic increments must fit the enum's backing type.
They never silently wrap; an overflow or a shift count outside the backing
width is reported at that member.

```
enum name {
    ABC, // = 0
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

A field can have a default after its type:

```
struct List<T> {
    data: T* = null;
    length: u64;
    capacity: u64 = 8;
}
```

A bare struct declaration uses its defaults, including defaults in nested structs, and zeroes the other fields. A named literal does the same for fields it leaves out. A positional literal must fill every field. A struct with no defaults remains uninitialized when declared bare.

Defaults cannot use local names. They may use literals, constants, enum members, and `@sizeof`. Union fields cannot have defaults. Globals remain zero-initialized.

#### Field type inference

A field's type can be inferred from its default:

```
struct State {
    mode = Mode::None;
    open = false;
}
```

Here, `mode` is a `Mode` and `open` is a `bool`. Keep the annotation when the default needs a specific type, as in `handle: Handle* = null`.

#### Generic structs

A struct is generic when type parameters follow its name:

```
struct List<T> {
    data: T*;
    length: u64;
    capacity: u64;
}
```

Type arguments are written after the struct name:

```
let list: List<i32>;
fn first(list: List<i32>) -> i32;
```

Each set of type arguments creates one concrete struct at compile time. The same arguments always refer to the same type. Type arguments may include other instantiations, such as `List<List<i32>>`.

A field may refer to its own instantiation through a pointer:

```
struct Node<T> {
    value: T;
    next: Node<T>*;
}
```

Modifiers such as `const` and `&` cannot be used in type arguments because their meaning could change when substituted into a type such as `T*`.

Struct parameters use the same [bounds as generic functions](#generic-functions):

```
struct Map<K: Hashable, V> {
    // every Map key implements Hashable
}
```

The bound is checked when a concrete `Map<K, V>` is formed, before its fields or methods are instantiated. Unions and generic interfaces use the same parameter syntax.

#### Tuples

`Tuple<A, B, ...>` is builtin and variadic: each arity is a struct of its element types, built by a parenthesized literal or declared like any type:

```
let t: Tuple<i32, f64>;
let p = (1, 2.5, "three");   // Tuple<i32, f64, char*>, inferred
let q: Tuple<u8, i64> = (7, 9);
let one = (42,);             // the single-element spelling
```

Elements read and write through `t[<n>]`, where the index is a compile-time constant expression inside the arity, and `.length` reads the arity, also a constant. Tuples pass and return by value, sit in fields, and nest (`((1, 2), 3)`); the name `Tuple` is reserved.

```
let x = p[0];
p[1] = 3.5;
p.length;    // 3
```

A `let` over a parenthesized pattern destructures: each name binds the matching element as a fresh local copy, the pattern's arity must match the tuple's, and patterns nest. The types come from the tuple, so the pattern takes no annotation:

```
let (lo, hi) = minmax(9, 3);
let ((a, b), c) = ((10, 20), 30);
let (only,) = (42,);
```

A function (or closure) parameter may use the same pattern. The call still passes one by-value `Tuple<...>`; the annotation is required and must match the pattern's arity. Inside the body the names are ordinary locals:

```
fn sum((a, b, c): Tuple<i32, u32, f32>) -> f32 {
    return a as f32 + b as f32 + c;
}

sum((1, 2 as u32, 3.0));
let t: Tuple<i32, u32, f32> = (1, 2 as u32, 3.0);
sum(t);
```

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

A named literal initializes exactly one union field:

```
union U { a: u64; b: f64; }
let u: U = { a = 100 };
```

The field name is required because the fields overlap; empty, positional and multiple-field union literals are refused. Bytes outside the selected field begin at zero. A union's size and alignment are its largest field's, inside enclosing structs too. `@align(N)` and `@volatile` apply like a struct's; `@packed` has no field layout to act on and is refused.

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

An unnamed type's identity is structural: two spellings with the same fields are one type, so a `struct { x: i32; y: i32; }` local passes to a `struct { x: i32; y: i32; }` parameter directly. They compose everywhere a named type would: locals, aliases, raw arrays, pointers, `@sizeof`, and each other.

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

Similar to regular functions, they're declared through the `fn` keyword.
They may live inside the struct body, where the enclosing struct supplies
their receiver type:

```
struct S<A> {
    value: A;

    fn set(&self, value: A) {
        self.value = value;
    }

    fn get(const &self) -> A {
        return self.value;
    }
}
```

The struct's type parameters and bounds are in scope throughout a nested
method. A method may also declare generic parameters of its own after its
name. A nested `@where` may further constrain the enclosing receiver
family, including for an `@override`, in the same form as an out-of-line
method.

The out-of-line spelling prefixes the method name with `S::`, where `S` is
the struct it belongs to. Both spellings declare the same method and may be
used together, so a struct can present a method's signature while keeping
its body elsewhere:

```
struct S<A> {
    fn set(&self, value: A);
    fn get(const &self) -> A;
}

fn S<A>::set(&self, value: A) {
    self.value = value;
}

fn S<A>::get(const &self) -> A {
    return self.value;
}
```

A body may be nested or out of line independently for each method. Fields
and methods may appear in either order inside the struct.

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

Their parameters take bounds in the same place:

```
fn S::method<T: Iface>(&self, value: T) {
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

#### Builtin type methods

Methods may be declared directly on builtin types in the same way as methods
on structs:

```
fn i32::doubled(const &self) -> i32 {
    return self * 2;
}

let answer = 21.doubled();
```

Use `@where` with a builtin bound to declare one method for a family of
builtin types:

```
@where<T: Scalar>
fn T::value(const &self) -> T {
    return self;
}
```

`Scalar` covers all primitive value types. `Integer`, `SignedInteger`, and
`UnsignedInteger` provide narrower builtin families.

Arrays support methods in the same way. Using `T` as the element type declares
the method for every array type:

```
fn T[]::count(&self, value: T) -> i32 {
    let n = 0;
    foreach (el : self) {
        if (el == value)
            n += 1;
    }
    return n;
}

let hits = ints.count(3);
let ls = text.count('l');
```

[Operator shorthands](#operator-overloading) also use array methods, so `eq`
provides `==` and `!=`, while `add` provides `+`.

### Interfaces

Interfaces define the fields and methods a type must provide:

```
interface Named {
    name: char[];
    fn greet(&self) -> char[];
}

struct Person: Named {
    name: char[];
}

fn Person::greet(self: &Person) -> char[] {
    return self.name;
}

fn welcome(person: Named) { person.greet(); }
```

A type implements an interface by listing it after `:` and providing every
required field and method. Multiple interfaces are separated by commas, as in
`struct Person: Named, Aged`. Required methods may also be declared outside
the interface body as `fn Named::greet(&self) -> char[];`.

Interfaces may be used only as parameter types. Calls are compiled for each
concrete argument type, with no runtime interface object or dispatch.

#### Generic interfaces

Interfaces can be generic just like structs, when their name is followed by `<T>`, their bodies speaking the type parameters:

```
interface Iterable<T> {
    fn iterator(&self) -> Iterator<T>;
}
```

Their parameters may be bounded too (`interface Index<K: Hashable>;`); every concrete claim must satisfy those bounds.

An interface with no requirements of its own may end in `;` without a body; outside-the-body actions then spell the receiver's parameters themselves:

```
interface Iterable<T>;

fn Iterable<T>::iterator(self: &Iterable<T>) -> Iterator<T>;
```

A claim's type argument may itself be an interface: `struct List<T>: Add<List<T>, Iterable<T>>` requires an `add` taking any iterable, and the overload whose parameter spells that same interface satisfies it.

#### Extending types

`@extend` adds methods and interface claims to an existing type. Methods in
the block use that type as their receiver:

```
@extend Number: Formattable {
    fn format(const &self) -> String { ... }
}
```

An interface claim may be written separately when its methods are defined
elsewhere:

```
@extend Number: Formattable;

fn Number::format(const &self) -> String { ... }
```

Extensions work with structs, enums, primitives, aliases, and generic type
families. A concrete receiver extends only that type; a receiver containing a
placeholder, such as `T[]`, extends every matching type.

`@where<T: Bound>` restricts a generic function, method, or extension to types
that satisfy `Bound`. Braces apply the same bound to a group, and nested
`@where` bounds combine:

```
@where<T: Scalar> {
    @extend T[]: Hashable;
    fn T[]::hash(const &self) -> u64 { ... }
}
```

Extending a primitive makes the claimed interface methods available but does
not change the primitive's built-in operators.

#### The iteration interfaces

`Iterator<T>`, `ConstIterator<T>`, and `Iterable<T>` are builtin and available
without an import:

```
interface Iterator<T> {
    fn has_next(&self) -> bool;
    fn next(&self) -> &T;
}

interface ConstIterator<T> {
    fn has_next(&self) -> bool;
    fn next(&self) -> const &T;
}

interface Iterable<T> {
    fn iterator(&self) -> Iterator<T>;
    fn const_iterator(const &self) -> ConstIterator<T>;
}
```

`Iterator<T>` returns mutable element references, while `ConstIterator<T>`
returns read-only references. `Iterable<T>` provides both forms. A `foreach`
uses `iterator()` for a mutable value and `const_iterator()` for a `const`
value.

[Arrays](#arrays) implement `Iterable<T>` automatically, so they work with
`foreach` and may be passed anywhere an `Iterable<T>` is expected. Iterator
references point to the original elements, allowing mutable iteration to
update the collection.

### Error handling

Sie uses the builtin `Result<V, E>` when an operation returns either a value
or an error. `Result<E>` represents success without a value. Create results
with `Ok` and `Error`; their types are usually inferred from the surrounding
return type or annotation.

A result is true on success and false when it holds an error:

```
fn divide(a: i32, b: i32) -> Result<i32, MathError> {
    if (b == 0) {
        return Error(MathError::DIVISION_BY_ZERO);
    }
    return Ok(a / b);
}

let result = divide(10, 2);
if (result) {
    use(result.value);
} else {
    report(result.error);
}
```

#### Checking the tag

Only one result member is valid at a time. The compiler allows `value` only
after a successful result check and `error` only after a failed one:

```
if (res.ok) {
    use(res.value);
} else {
    report(res.error);
}
```

The check also remains known after a branch that returns, breaks, or otherwise
leaves. Assigning to the result or exposing its address invalidates what was
known, so it must be checked again.

#### Unwrapping with try

`try <result> except (<name>) { ... }` unwraps a successful result. On failure,
the `except` block runs with the error bound to `name`:

```
let value = try divide(10, 2) except (error) { return 1; }
let value = try divide(10, 2) except (error) {
    report(error);
    emit 0;
}
```

For `Result<V, E>`, the block must leave the current control flow or use
`emit` to provide a replacement value. A `Result<E>` has no value to replace,
so its block may finish normally.

#### The fallback shorthand

`try <result> ?? <fallback>` unwraps the result or uses the fallback on error.
The fallback is evaluated only when needed:

```
let value = try divide(10, 2) ?? 0;
let value = try divide(10, 2) ?? recover();
```

Use a block to run statements before producing the fallback:

```
let value = try divide(10, 2) ?? { report(); emit 0; };
```

For `Result<E>`, the fallback runs only for its side effects.

#### Error propagation

A bare `try` unwraps a successful result or immediately returns its error to
the caller:

```
fn read_config(path: char*) -> Result<Config, IOError> {
    try file.open();                 // an IOError here returns from read_config
    let size = try file.size();      // and here, otherwise 'size' is the value

    return Ok(parse(file, size));
}
```

The enclosing function must return a `Result` with the same error type. A
bare `try` used as a statement ends with `;`.

## Copyright

Most of the project is licensed under the [BSD 3-Clause License](LICENSE). The [GLib](packages/glib/LICENSE) and [GTK](packages/gtk/LICENSE) packages are instead licensed under the GNU LGPL v2 only.
