# sielang

Sie is a C-flavored language for systems programming. It has minimal syntax, a strong type system, and type inference. It provides low-level hardware control together with defer statements, tagged unions, generics, typed variadics, `Any`, and `foreach` loops.

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

Precompiled object files and static libraries can be given with the sources. They skip compilation and link into the executable. With `--run`, their symbols are resolved in the just-in-time (JIT) compiler. The JIT compiler also unpacks members from static libraries.

```
siec main.sie file1.o file2.o -o main
siec main.sie libfoo.a -o main
```

- `-o <path>` names the output executable, `a.out` by default.
- `-c` compiles to an object file without linking. The object file uses the source name (`main.sie` → `main.o`) unless `-o` specifies a different name. The object defines the named sources and their includes. An imported module supplies declarations only. Its definitions come from its own `-c` object during linking. See [Imports](#imports).
- `-I <dir>` adds a directory to the include search path. The `lib/` directory next to each source file is always searched.
- `-O <n>` sets the optimization level, cc-style: `-O0` (the default) emits code as generated, and `-O1` through `-O3` run LLVM's standard optimization pipeline. It applies to every output form, including executables, objects, `--emit-llvm`, `--emit-asm`, and `--run`.
- `-g` emits DWARF debugging information. Each instruction maps to its source line. Each function, parameter, and variable includes its type. This permits source-level debugging in LLDB or GDB. The debugger supports file and line breakpoints, stepping, Sie lines in `bt`, and values in `frame variable`. Use `-O0` for debugging so that instructions keep their source order. On macOS, keep the `.o` file next to the executable because the debugger reads DWARF information from it.
- `-l <lib>` links against a library, passed through to the linker: `-l m` links the C math library. Under `--run`, the library is loaded into the isolated JIT worker instead, its symbols resolvable the same way.
- `-L <dir>` adds a directory to the library search path.
- `--target <triple>` compiles for a target triple instead of the host, for example `x86_64-unknown-linux-gnu`. The target applies to object code, [target constants](#target-constants), and every `@sizeof`. Use `-c` to produce cross-compiled objects because linking still uses the host `cc`. The `--run` option accepts only the host triple because the isolated JIT worker executes native code.
- `--emit-llvm` prints the LLVM IR and exits, without building.
- `--emit-asm` prints the target's assembly and exits, without building.
- `--run` JIT-compiles and runs the program in place of building it, exiting with the program's own exit code. Anything after the flag is passed along as its arguments:

```
siec main.sie --run arg1 arg2
```

### Compilation phases

Compilation is declaration-order independent: moving a type or extension before or after the code that uses it does not change meaning. The loader builds the unit, then the compiler runs five phases:

0. **Discover and select.** Recursively find imports and includes from the requested sources. A condition guarding an include uses only the [loader-safe constant environment](#conditional-compilation).
1. **Parse.** Every selected file has a syntax tree before types are asked about.
2. **Collect.** Gather definitions, types, callables, generics, and interface claims across the whole unit.
3. **Resolve.** Resolve aliases, fields, signatures, generic arguments, and bounds against that inventory.
4. **Check.** Check bodies, conformance, and assertions once resolution is done.

An extension applies where its bound holds, even if it is declared later or imported from another module. Generic instances use the same phase order. The compiler processes them until the worklist is empty. LLVM emission starts after this point and does not create more instances.

### Editor support

`sie-lsp` is a Language Server Protocol (LSP) server on the compiler front end. It recompiles open buffers when they change. It provides diagnostics, an outline, completion, signature help, hover information, and go-to-definition. In a function, method, or macro call, signature help lists the parameters and identifies the active parameter.

```
pip install -e '.[lsp]'
```

`editors/` holds the clients: `editors/vscode/sie` for VS Code, `editors/nvim` for Neovim, `editors/helix/languages.toml` for Helix, and `editors/tree-sitter-sie` for the grammar. Any LSP editor can run `sie-lsp` over stdio for `.sie` files. Include paths come from the nearest `package.toml` and the workspace root's, the way `sie build` resolves them; extras pass as `includePaths` in the initialization options.

## The package manager

`sie` is the project-level tool. Where `siec` compiles a list of sources, `sie` works from a package. A package is a directory that contains a `package.toml` manifest. The manifest defines the package name and contents.

`[package]` identifies the package. One of `[app]` or `[library]` defines the package type. A **library** is installed so that other packages can use it:

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

The two package types are mutually exclusive, and one is required. A library has no entry point to build. An app produces the final executable and cannot be a dependency. A manifest that declares both types is invalid. The `sources` and `libs` fields belong to the selected package type because they define its contents.

With no command, `sie` takes a package directory as its argument and prints the manifest information. The package directory defaults to the working directory:

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

Packages are ordered by name and then by version. Version components are compared as numbers, so `9.0.0` comes before `10.0.0`. The directory name identifies the package. A package with no manifest is still listed but has no description. An empty install root is not an error. In this case, the list is empty and a note is written to standard error.

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

Pass `--run` to JIT-run the resolved package without writing a binary. As with `siec --run`, everything after the flag is passed to the program and the command returns the program's exit status:

```
$ sie build examples/helloworld --run arg1 arg2
running helloworld
  core@1.0.0
  libc@1.0.0
```

An app needs a `name` for the binary, but no `version`. Pointing `build` at a `[library]` is an error: it is installed, not built.

`[dependencies]` names packages and the accepted versions (`*`, `~1`, `^1.2.3`, comparisons, or a bare version). Dependencies resolve from the install root. When multiple versions match, the newest version is selected. Their sources and `libs` are included in the build. Library search directories come from `LIBRARY_PATH`, as in a C build. The `-O` and `-g` options pass through to the compiler.

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

`move` takes an owned local as a whole. After a move, the variable cannot be read until it is reassigned. Three built-in interfaces customize the store:

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

Sie uses resource acquisition is initialization (RAII) for deterministic cleanup. Types enable this behavior when they claim the built-in `Destroy` interface:

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

Moving, returning, or passing the value by ownership transfers that cleanup. `destroy` is responsible for the whole value, including owned fields. The compiler does not destroy the fields separately. `drop place;` and `value.destroy()` destroy a mutable place and disable its automatic cleanup.

#### Raw storage slots

`Slot<T>` has the size and alignment of `T`, but it has no automatic lifetime. The caller must mark each state transition. `Slot<T>` does not implement `Destroy`.

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

Constants are compile-time expressions declared through `@const`. Unlike a `let` variable, a constant has no storage. The compiler substitutes its value at compile time, similar to a type-safe C `#define`. Constants must be initialized and cannot be reassigned. The type annotation is optional. When it is omitted, the compiler infers the type from the value:

```
@const name: T = <value>;
@const name = <value>; // type inferred
```

Each module keeps its own constants. For example, the stdio and unistd modules can both declare `SEEK_SET`. A use resolves to the nearest visible declaration. The compiler searches the file and its includes first, then member imports, and then the compilation unit. Two declarations at the same distance are ambiguous. A qualified name selects a declaration from one module.

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

`OS_NONE` marks bare-metal targets, such as `riscv64-unknown-none-elf`. `TARGET_ENV` is the optional fourth field of the triple. Known values include `gnu`, `musl`, `msvc`, `android`, and `elf`. A triple without this field uses `ENV_UNKNOWN`. The unknown constants identify values that the compiler does not classify.

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

Constant integer arithmetic follows run-time signed division and remainder: division truncates toward zero, and a remainder has the dividend's sign. `and` and `or` short-circuit here exactly as they do at run time. Division by zero, negative or out-of-range shifts, and results wider than every Sie integer type are compile-time errors rather than Python or backend failures.

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

A branch can contain any top-level declaration. This includes functions, structs, enums, globals, constants, type aliases, and nested `@if` blocks. A constant in a selected branch is visible to subsequent conditions.

Conditions that do not require type information are selected before declarations are collected. A condition that uses an enum member, `@sizeof`, or `@typeid` is evaluated after the active types are resolved. It can therefore inspect a declaration written later. When a branch is selected, its declarations are registered before its nested conditions are evaluated. A condition cannot depend on a declaration whose existence the condition controls.

An `@include` can also occur in a branch. The loader loads files only from the selected arm. It does not resolve an include in an unselected arm. Therefore, the file does not have to exist on the current platform:

```
@if (TARGET_OS == OS_DARWIN) {
    @include("darwin/_errno");
} @else @if (TARGET_OS == OS_LINUX) {
    @include("linux/_errno");
}
```

An include determines which files form the program. A condition that controls an include is therefore evaluated while files are loading. At that point, the condition can use literals, operators, target constants, and loaded `@const` values. Loaded constants can come from the current file, its includes, or previously selected branches. Enum members and `@sizeof` require the assembled program and cannot be used in this condition. An `@if` that does not control an include can use the [full constant language](#conditional-compilation). Imports are unconditional. For platform-specific imports, import a module that selects an include conditionally.

#### Error

`@error("message")` stops compilation and reports its message. The compiler does not resolve an unselected branch. Therefore, an `@error` in an `@if` takes effect only when the compiler selects that branch. Use this behavior to reject a platform that has no binding:

```
@if (TARGET_OS == OS_DARWIN) {
    // ...
} @else @if (TARGET_OS == OS_LINUX) {
    // ...
} @else {
    @error("Unsupported OS")
}
```

The compiler reports the message with its file and line. Outside an `@if`, `@error` always prevents the file from building. An `@error` in an imported module identifies that module, not its importer. A trailing `;` is permitted.

#### Static assert

`@static_assert(cond, "message")` requires a compile-time condition to be true. It is equivalent to C's `static_assert`. A true condition has no effect. A false condition stops compilation and reports `static assertion failed: <message>`.

```
struct Header { a: u64; b: u64; }

@static_assert(@sizeof(Header) == 16, "Header must stay two words");
```

Unlike an `@if`, an assert does not declare an item. The compiler checks it after the whole program is registered. Its condition can inspect a struct's `@sizeof`, enum members, and constants in any declaration order. An assert inside an `@if` is checked only when the compiler selects that branch.

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

An arithmetic or bitwise operator can combine with `=` to form a compound assignment. The compound assignment updates the variable in place:

- Arithmetic: `+=`, `-=`, `*=`, `/=`, `%=`, `**=`
- Bitwise: `<<=`, `>>=`, `&=`, `|=`, `^=`

```
let a: i32 = 10;
a += 5; // a holds the value 15, equivalent to a = a + 5
```

### Truthiness

Values other than `bool` can still be used wherever a truthy value is expected:

- Numbers and `char`s are truthy when they are `!= 0`.
- Booleans are truthy when they are `true`.
- Pointers are truthy when they are non-null.
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

The subject is evaluated once. The compiler compares each `when` expression with the subject in declaration order. Statements in each arm run in a separate scope. The scope ends at the next `when`, `else`, or closing brace.

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

The built-in `enumerate(x)` wraps an Iterable (or an iterator) in an iterator of `{index: u64, value: T}` pairs, counting from zero:

```
foreach (e : enumerate(nums)) {
    printf("%llu: %d\n", e.index, e.value);
}
```

`value` is a copy of the element, not a reference into the collection. A mutable iterator produces `Enumerated<T>` pairs through `EnumerateIterator<I, T>`; a const iterator produces `ConstEnumerated<T>` pairs through `ConstEnumerateIterator<I, T>`, whose `value` remains `const T`. A declared function named `enumerate` takes precedence over the built-in function.

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

Use this form to place resource release code next to the acquisition code:

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

For the same reason, a deferred statement cannot use `return`, `emit`, `break`, or `continue` to leave its surrounding scope. Each operation would interrupt the active defer sequence.

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

Entry-point types may use aliases and may carry an outer `const`; they are checked after aliases resolve, including aliases imported from another module. The entry must remain one public external definition, so `main` cannot be `@extern`, `@inline`, `@static`, `@private`, `@override`, `@remove`, or `@symbol`. A matching forward declaration may still precede its definition.

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

Each call ranks all matching overloads together, including generic and interface-bound ones. The least exact argument sets the candidate's rank: exact match, implicit conversion, then literal adoption. Implicit conversions include widening, array decay, `opaque*`, and `null`. Literal adoption occurs when an untyped literal takes the parameter type instead of its default type.

When candidates have the same rank, the candidate with fewer literal adoptions wins. If those counts are equal, the candidate with fewer implicit conversions wins.

```
fn pick(left: i32, right: i64) -> i32 { return 1; }
fn pick(left: i64, right: i64) -> i32 { return 2; }

let left: i32 = 0;
let right: i32 = 0;
pick(left, right); // the first overload: one widening instead of two
```

A concrete overload wins when its conversion profile equals a generic overload's profile. If no candidate matches, or candidates remain tied after these comparisons, the call is a compile-time error.

An interface match keeps the argument's type, so it wins over a concrete overload that would have to convert it:

```
fn kind(value: i64) -> i32 { return 1; }
fn kind<T: SignedInteger>(value: T) -> i32 { return 2; }

let value: i32 = 0;
kind(value); // the SignedInteger overload; value stays an i32
```

An untyped integer literal counts as its first fitting signed type: `i32`, `i64`, then `i128`. From there it converts like any other value:

```
dec.add(5);            // an i32, widened into the i64 overload
dec.add(5000000000);   // does not fit an i32: exactly the i64 overload
dec.add(other);        // a Decimal: exactly 'const &Decimal'
```

Signed and unsigned never mix: a `u8` argument widens into a `u64` candidate, never an `i64` one. A reference parameter (`&T`) needs its exact type, since it aliases the argument in place.

The return type is not part of the signature, so two overloads that differ only by return type conflict. `@extern`, `@symbol`, and `main` functions cannot overload because each maps to one symbol. A bare reference to an overloaded name, such as `let g = f;`, is an error because no arguments are available to select an overload.

A function's module symbol carries its parameter types: `pick(i64)`, `List<char>::init(&List<char>,u64)`. Separately compiled units therefore name every signature alike, whatever their declaration order; only `@extern`, `@symbol`, and `main` keep their unmangled C symbols.

#### Overrides

`@override` replaces one matching function or method implementation. The target must exist in the compilation unit with the same parameter and return types. Without the decorator, two definitions of the same function cause an error. Collection occurs before override selection, so the declarations can occur in either order:

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

Compound assignment uses the methods `add_assign` through `rem_assign`. Each method takes the value and returns nothing. For example, `a += b` calls `a.add_assign(b)` and updates `a` in place. This behavior is important when the binary operator creates a new value. If `add` returns a new `Decimal`, the fallback operation assigns that result to `a` and drops the previous value. An in-place method avoids this temporary value.

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

The prelude declares one interface for each operator. `Add<S, T>` requires `add(&self, value: T) -> S`. The `Sub`, `Mul`, `Div`, and `Rem` interfaces use the same form. `AddAssign<T>` requires `add_assign(&self, value: T)`. The `SubAssign`, `MulAssign`, `DivAssign`, and `RemAssign` interfaces use the same form. `Eq<T>` requires `eq(&self, value: T) -> bool`. `Ord<T>` requires `cmp(&self, value: T) -> i32`. A claim declares and enforces the contract for one supported right-hand type:

```
struct Decimal : Add<Decimal, Decimal>, Add<Decimal, i64>, AddAssign<i64>,
                 Eq<Decimal>, Ord<Decimal> {
    // ...
}
```

The shorthand itself is structural, like `foreach` and `iterator()`: any struct with the method takes the operator, claimed or not. The interfaces are there to declare the contract and to bound generics.

Numeric primitives implement these interfaces intrinsically so `1.add(2)` is equivalent to `1 + 2`, and `n.add_assign(2)` to `n += 2`. A narrower method argument widens to the receiver's type as usual.

#### Indexed operators

Indexing has the same shorthand for structs. `a[key]` is `a.get_item(key)`, while `a[key] = value` is `a.set_item(key, value)`. The selected overload supplies the key and value types. A compound assignment reads the value, applies the binary operator, and writes the result. Thus, `a[key] += value` is `a.set_item(key, a.get_item(key) + value)`.

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

The prelude interface `GetItem<K, V>` requires `get_item(const &self, key: K) -> V`. The `SetItem<K, V>` interface requires `set_item(&self, key: K, value: V)`. A type can claim only the read capability. It can claim both interfaces if it also writes. The shorthand is structural, as with the other operator interfaces. A claim enforces the method signature and permits the capability to bound an interface parameter.

Native arrays, raw arrays, pointers, and tuples keep their built-in storage indexing: their `[]` never routes through these methods.

#### Generic functions

Functions are generic when their name is followed by placeholder types such as `T` or `U`. The types are enclosed by `<>` and separated by commas. The compiler replaces each placeholder with a concrete type at compile time.

```
fn f<T>(t: T);
fn f<T>() -> T;
fn f<T, U>(t: T, u: U);
fn f<T, U>(t: T) -> U;
```

A type parameter has lexical scope. Inside its template, it has precedence over an external type or interface with the same name. A template can be a function, method, receiver family, struct, interface, or alias. The rule applies to derived and nested forms such as `T[]`, `Box<T>`, and `fn(T) -> T`. Outside the template, the global declaration keeps its normal meaning.

A parameter may carry a bound after `:`. An interface bound accepts any type that implements it; any other type-like bound (an intrinsic, alias, or struct) accepts that canonical type exactly. Bounds may refer to the other parameters, and apply whether the call infers its arguments or spells them:

```
fn hash<K: Hashable>(key: K) -> u64;
fn size<T, U: Iterable<T>>(values: U) -> u64;
fn word<T: u64>(value: T) -> T;
fn ordered<T: Hashable & Comparable<T>>(value: T) -> T;
```

`&` forms an explicit intersection: the argument must satisfy every listed type-like bound. Intersections work wherever bounds do, including generic functions, structs, aliases, interfaces, receiver methods, extensions, and `@where` environments. They are unordered (`I1 & I2` and `I2 & I1` describe the same bound), and each additional member makes an otherwise matching overload or override more specific.

An alias in a bound means its target, so `@type Word = u64; fn word<T: Word>(...)` has the same bound as the third declaration. A concrete interface claim can also fill parameters named only inside the bound: an argument implementing `Iterable<char>` binds `T` to `char` in `U: Iterable<T>`.

`Scalar` is a sealed built-in interface for the primitive value types: the signed and unsigned integers, `f32`, `f64`, `bool`, and `char`. It is useful when an implementation needs the built-in scalar representation while accepting every width. Structs, enums, pointers, and arrays do not satisfy it, and user declarations cannot claim it.

`Integer` is the same kind of sealed marker for every integer primitive (`i8`…`i128` and `u8`…`u128`). `SignedInteger` and `UnsignedInteger` narrow that further to one signedness each. Like `Scalar`, only the compiler's primitives satisfy them, and user declarations cannot claim them.

A call instantiates the function for its concrete types. The compiler creates one instance for each argument list. It infers type arguments by matching each parameter form against its value argument. For example, `identity(n)` for an `i32` compiles `identity<i32>`. A parameter `items: T*` with an `i32*` argument binds `T` to `i32`. Literals use their default type when no context supplies a type.

A typed context also supplies information for inference. A typed context can be a declared return type, an annotated `let`, or a function parameter. For example, the return type supplies both parameters of `Result<V, E>` in `return Ok(v);`. If the expected type and an argument supply different types, the expected type has precedence. The compiler converts the argument to that type. Specify the type arguments when no context determines a parameter, as in a call to `fn empty<T>() -> T*`:

```
let p = empty<i32>();
let x = identity<i64>(5);
```

Same-named generic functions with different type-parameter counts coexist, like [generic structs](#generic-structs) of different arities: the call's shape (its explicit `<...>` count, its argument count, and what resolves) picks the template.

Generic functions may recurse and call one another, and their return types may name generic structs (`fn make<T>(t: T) -> Box<T>`). The same modifier rule as [generic structs](#generic-structs) applies to type arguments, and a template nobody calls compiles to nothing. `@extern` functions cannot be generic: they name one foreign symbol.

A generic function can also be a [function reference](#function-references). Outside a call, `identity<i32>` is the function value of that instance. A function-typed context can also resolve a bare generic name. Such a context includes a `fn(...)` annotation, a parameter, or a [generic alias](#generic-type-aliases). The compiler unifies the template signature with the target type:

```
let g = identity<i32>;             // explicit instance
let h: fn(i64) -> i64 = identity;  // T unified from the annotation
apply(identity, 40);               // T unified from apply's parameter type
```

Qualified spellings work the same way: `util.identity<i32>` and a bare `util.identity` in a function-typed context both resolve through the module binding.

#### Variadic functions

A final parameter written as `name...` is shorthand for `name: const Any[]`. Each extra call argument is wrapped as an [Any](#any). The compiler packs the values into a borrowed array view. If there are no values, it creates an empty view. The called function can inspect and forward the array but cannot modify it.

```
fn println(str: const char[], args...) {
    // args: const Any[]; args.length counts the extras
}

println("hello world");        // args.length = 0
println("hello {}", "world");  // args.length = 1
```

The body dispatches on each element with [`@typeof`](#any). Passing an `Any[]` or `const Any[]` forwards the existing array without packing it again. This permits one variadic function to call another. Methods also support this shorthand. `@extern` functions keep the C `...` form and use the C argument convention.

#### Extern

Functions can be decorated with `@extern` to declare that they are resolved at link time. Extern functions must follow the C application binary interface (ABI) and can use only C-compatible types.

```
@extern fn printf(fmt: char*, ...);
@extern fn malloc(size: u64) -> opaque*;
@extern fn free(ptr: opaque*);
```

Struct and union values cross the boundary by value in both directions. The compiler follows the target C calling convention for parameters and return values. It uses registers for small values and memory for large values. A C function that takes or returns a struct therefore uses a direct Sie declaration:

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

`@symbol("name")` separates a function's Sie name from its module symbol. The function links and emits under the specified symbol, while the program calls it by its Sie name. With `@extern`, it binds a foreign symbol to a selected Sie name. With [conditional compilation](#conditional-compilation), one Sie name can refer to different platform symbols:

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

It also exports defined functions under the selected symbol. `main` cannot be renamed because the C run time finds it by name. `@symbol` cannot combine with `@static` because the compiler controls a static symbol.

#### Inline

Functions can be decorated with `@inline` to inline them into every caller. Unlike C's `inline`, this is not a hint: the function is always inlined, even at `-O0`.

```
@inline fn square(n: i32) -> i32 {
    return n * n;
}
```

#### Static

Functions can be decorated with `@static` to make them local to their file. Other files cannot access them, and each file can reuse the name for its own static function. Use `@static` for private file helpers.

```
@static fn helper() -> i32 {
    // only callable from this file
}
```

Decorators can be combined. For example, `@static @inline fn` is both static and inline. An `@extern` function has no body, so body decorators do not apply to it. Only `@noreturn` can be combined with `@extern` because it describes the signature.

`@static let` declares a file-local global variable. One storage location is shared by every call and is visible only in its file. Its initializer must be a compile-time constant. As with a local `let`, the initializer can supply an omitted type. Without an initializer, the type is required and the storage starts at zero. An `@extern let` always keeps its explicit ABI type.

```
@static let count: i32 = 0;
@static let ready = false;

fn bump() -> i32 {
    count += 1;
    return count;
}
```

#### Private

`@private` keeps a declaration out of its module's import surface without changing its symbol or textual visibility. The defining file and files joined to it through `@include` may still use it. Qualified imports and member imports cannot.

```
@private @const DEFAULT = 0;
@private fn helper();
@private fn Parser::advance(&self);
@private struct State;
```

This differs from `@static`. A static function has file-local linkage and is visible only in its own file. A private declaration remains shared across a textual include module.

#### Noreturn

The `@noreturn` decorator declares that a function does not return control to its caller. Such a function exits, loops continuously, or calls another `@noreturn` function.

```
@extern @noreturn fn exit(code: i32);

@noreturn fn die(code: i32) {
    exit(code);
}
```

A call to an `@noreturn` function ends its control path. Therefore, it satisfies a required return in a function or branch:

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

Use `@remove("advice")` after a deprecated function is removed. The declaration remains so that the compiler can identify uses of its name. It has no body because it has no definition. Each use produces a compile error that includes the advice:

```
fn new_func() { }

@remove("use new_func")
fn old_func();

fn main() {
    old_func(); // error: 'old_func' was removed: use new_func
}
```

Unlike a deprecation, a removal is not gated by reachability. A use anywhere fails, including a function reference. An unused removed declaration compiles so that future callers get the removal advice instead of an `undefined function` error.

Methods, generic functions, and methods of generic structs or arrays use the same removal syntax. A removed generic does not register a template or create instances. A use of its name reports the removal advice:

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

`@asm` also works as an inline block. It inserts assembly in an expression or statement instead of replacing a complete function. A parenthesized argument list passes values from the enclosing scope. Each name inserts its value in the block, as in a decorated function:

```
@asm { /* no operands */ }

@asm (x, y) {
    add ${out:w}, ${x:w}, ${y:w}
}

@asm @clobbers("x0", "memory") { /* ... */ }

@asm @clobbers("x0", "memory") (x, y) { /* ... */ }
```

An inline block produces a value when `-> T` follows its argument list. As in a decorated function, `${out}` represents the result:

```
let sum: i32 = @asm (x, y) -> i32 {
    add ${out:w}, ${x:w}, ${y:w}
};

let masked: i32 = @asm @clobbers("x0", "memory") (x, y) -> i32 {
    // ...
};
```

### Types

#### Built-in types

- Signed integers: `i8`, `i16`, `i32`, `i64`, `i128`.
- Unsigned integers: `u8`, `u16`, `u32`, `u64`, `u128`.
- Floats: `f32`, `f64`.
- Booleans: `bool`.
- Characters: `char`.

Integer literals may also be written in hexadecimal with the `0x` prefix:

```
let mask: u32 = 0xFF00;
```

One integer token may contain at most 4096 digits. This parser boundary keeps malformed or generated source from exhausting the host integer converter.

Without a type context, an integer literal defaults to the first signed type it fits: `i32`, then `i64`, then `i128`.

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

Sie has no `void` type. Use `opaque*` for opaque pointers.

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

Comparisons are the exception: an `opaque*` can be compared with any pointer type, on either side and with any comparison operator. Two typed pointers must still have the same type; for example, comparing a `u8*` with an unrelated `S*` is rejected.

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

A value of an `iN`, `uN`, or `fN` type widens implicitly when it is assigned or passed to a larger type with the same prefix. An `iN` widens to an `iM`, a `uN` widens to a `uM`, and an `fN` widens to an `fM`. Signed values sign-extend, unsigned values zero-extend, and floats extend.

```
let a: u8 = 0;
let b: u64 = a; // implicit widening, equivalent to let b: u64 = a as u64;
```

Operands use the same widening rules. If both operands have the same prefix but different widths, the compiler widens the narrower operand.

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

The `*` operator dereferences a pointer. `*p` is equivalent to `p[0]` and supports the same read and assignment operations. Prefixes can be combined, so `**pp` dereferences two pointer levels.

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

Arrays are collections of values with the same type. They are written as `X[]` and have the internal form `{X*, u64}`. The `X*` value points to `X`, and the `u64` value contains the element count. The `data` and `length` members expose these values:

```
let arr: i32[];

let ptr: i32* = arr.data;   // the backing pointer
let n: u64 = arr.length;    // the element count
```

An `X[N]` declaration allocates `N` elements on the stack. The array data points to these elements, and its initial length is `N`. A sized declaration does not take an initializer. The size must be a positive constant integer expression. It can contain literals, `@const` values, or a combination of them.

```
@const HEADER = 16;

let buf: u8[64];              // buf.data -> 64 stack bytes, buf.length == 64
let body: u8[64 - HEADER];    // sized by a constant expression
```

A sized struct field owns fixed inline storage in its containing struct. Therefore, moving the struct does not leave an internal pointer behind. Reading the field produces an ordinary `X[]` view. Its `data` points to the inline storage, and its `length` is `N`. Indexing accesses the storage directly.

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

Arrays can be initialized with elements enclosed by `[]` and separated by commas. A trailing comma is permitted.

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

`@raw<T>[N]` is equivalent to C `T[N]`. It contains exactly N inline elements with no pointer or run-time length. An `X[]` is a `{pointer, length}` pair that refers to storage. A raw array is the storage itself, as required for fixed-size C ABI fields:

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

A literal is a `char[]` in all contexts. It has a length, supports indexing, and uses the [built-in type methods](#built-in-type-methods). Operator shorthands also apply, such as `"a" + s` and `s == "a"`. Only an explicit `char*` context takes the pointer. Examples include `let p: char* = "Hello";` and an `@extern` function with a `char*` parameter.

String arrays can also be initialized with a `{ptr, n}` pair:

```
let ptr: char* = "Hello";
let n: u64 = 5;
let msg: char[] = {ptr, n};
```

They are null-terminated for C compatibility, but their length does not include the null character. The separate `char` type gives `char[]` string behavior that a plain byte array does not have.

Casting between `i8[]`/`u8[]` and `char[]` automatically handles the length change, but assumes that the underlying pointer is null-terminated.

#### References

References to a type `T` are represented by `&T`. The `&` operator cannot obtain the address of storage reached through a reference. For `s: &S`, both `&s` and `&s.member` are compile errors because they would expose the caller's storage.

References cannot type a variable.

```
let t: &T; // invalid
```

As function parameters, references indicate that the value is passed by reference instead of by value. Internally, they are represented by a hidden pointer.

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

A reference parameter normally aliases assignable storage in the caller. A `const &T` parameter only reads, so it also accepts literals and convertible values. The compiler converts the value when necessary and creates storage with the parameter type. A mutable `&T` requires caller storage of exactly that type. A conversion would create temporary storage and discard writes to it.

A function can return a reference with `-> &T` if the function has a source reference parameter. This source is usually the receiver. The function cannot return a reference to storage that expires after the call, such as a local variable or a parameter copy. The `return` takes the address of the value. Reading the result copies the value. Calling a [method](#methods) on the result, or returning it again, preserves the alias to the original value.

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

`callback.env` exposes the opaque environment pointer for foreign callback APIs. Their `user_data` parameter carries this pointer. An explicit cast adapts the closure to the foreign signature. The signature must end in `opaque*`. Its first parameters must match the declared closure parameters. The closure ignores additional parameters between the declared parameters and `opaque*`. A generic macro can contain the cast while keeping the ABI visible at the call site:

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

Captured variables move to stable shared storage when the closure is formed. The original scope and every closure that captures the variable observe the same value. The `callback.env` value remains valid if a foreign callback retains it after the creating function returns. The storage remains allocated for the process lifetime.

#### Type aliases

Type aliases give an existing type expression a new name. They are declared through `@type`, followed by the name, `=`, and the aliased type expression. The declaration ends in `;`:

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

A concrete type supplies arguments where the type is written. For example, `cmp<i32>` is `fn(i32, i32) -> bool`. The target can be any type that uses the parameters. This includes a [generic struct](#generic-structs) or another generic alias, such as `@type boxes<T> = List<Box<T>>;`. The same modifier rule applies to arguments. The compiler reports cycles as alias cycles.

Alias parameters take the same [bounds as generic functions](#generic-functions), checked before the target expands:

```
@type Entry<K: Hashable, V> = Pair<K, V>;
```

#### Type casting

Any represented value can be explicitly viewed as another type through the `as` keyword. Reading that view produces a value copy. Scalar representations convert at their LLVM width even when the Sie type is not arithmetic, so `char` and `bool` cast like their underlying integers. Addressable aggregate values reinterpret the same storage through the target representation.

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

An [Any](#any) operand is resolved at run time. The compiler looks up the wrapped type identifier in a table of types that the program wraps. An unknown identifier returns `"?"`. With [separate compilation](#imports), each unit has its own table. An `Any` value that crosses `-c` units can therefore return `"?"` in a unit that does not wrap its type.

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

`Any` is a built-in struct that erases a value's type behind its identifier:

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

`@typeof(x)` returns the type identifier of an expression. For an `Any` operand, it reads the run-time `id` field. For all other operands, the compiler uses the static `@typeid` value and does not evaluate the operand. A comparison with a bare type name uses the identifier of that type. This applies to `==`, `!=`, and `when` arms. Type forms such as `char[]` and `i32*` are also permitted:

```
@typeof(arg) == @typeid(u64);
@typeof(arg) == u64;             // the same, sugared
case (@typeof(arg)) {
    when u64: // ...
    when char[]: // ...
}
```

`a as T` reads the erased value as `T` without a run-time check. The caller must compare `@typeof(a)` before the cast. The pointed value is in the frame of the function that wrapped it. If an `Any` value outlives that frame, its pointer becomes invalid. Wrapping also removes the `const` contract. The code that unwraps the value selects a new contract.

A `when` can also name an [interface](#interfaces). The compiler creates one arm for each known type that implements the interface. It replaces the interface name in the body with the concrete type. Therefore, the cast in each generated arm reads that concrete type:

```
case (@typeof(args[i])) {
when Formattable:
    let arg = args[i] as Formattable;  // 'as i64' in the i64 arm, ...
    result.append(arg.format(modifier));
}
```

The expansion covers every type claiming the interface, arrays included through the family's claim, so `when Iterable<char>:` arms `char[]` among the implementers. A type an earlier arm already matched never reaches its stamped arm: the first match still wins.

A nested interface argument expands for each type combination. For `when Iterable<Formattable>:`, the compiler substitutes each formattable type into the argument. It then creates an arm for each iterable of that type. Thus, the arm includes `i64[]` and `P[]` when `i64` and `P` claim `Formattable`.

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

An enum member can define a specific value with `= <value>` after its name. The value is a constant integer expression. It can combine literals, `@const` constants, and members of any enum:

```
enum name {
    ABC,
    DEF = 5,
    GHI = name::DEF | 0x10,
}
```

Every enum and member name is collected before those values resolve, so an expression may also reference a member or enum declared later. A cycle between member values is rejected with the chain that forms it.

Members are assigned values automatically, starting at 0 and increasing by 1 for each subsequent member. Setting a specific value for a member changes the counter for the following ones, which then keep increasing from there.

Both explicit values and automatic increments must fit the enum's backing type. They never silently wrap; an overflow or a shift count outside the backing width is reported at that member.

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

Structs contain structured data of multiple types. A struct declaration starts with `struct` followed by its name. Each member has a name followed by `: T`, where `T` is its type. Semicolons separate the members.

```
struct S {
    a: A;
    b: B;
    // more members
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

`Tuple<A, B, ...>` is built-in and variadic. Each arity is a struct of its element types. A parenthesized literal builds it, or a declaration names it like any type:

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

A `let` with a parenthesized pattern destructures a tuple. Each name binds the corresponding element as a new local copy. The pattern and tuple must have the same arity. Patterns can be nested. The tuple supplies the types, so the pattern has no annotation:

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

Unions use the `union` keyword and have the same declaration form as structs. All fields share one storage location. Writing one field and reading another reinterprets the same bytes.

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

The field name is required because the fields overlap. Empty, positional, and multiple-field union literals are invalid. Bytes outside the selected field start at zero. The union uses the size and alignment of its largest field, including when it is in a struct. `@align(N)` and `@volatile` apply as they do to a struct. `@packed` is invalid because a union has no separate field layout.

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

An unnamed type has structural identity. Two declarations with the same fields define the same type. Therefore, a `struct { x: i32; y: i32; }` local passes directly to a `struct { x: i32; y: i32; }` parameter. Unnamed types can occur in locals, aliases, raw arrays, pointers, `@sizeof`, and other unnamed types.

An unnamed struct or union can be an unnamed member. Its fields become direct fields of the enclosing struct. This rule also applies to nested unnamed members:

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

Structs can have methods. A method is a function that acts on a specific struct type.

Methods are declared through the `fn` keyword. They may live inside the struct body, where the enclosing struct supplies their receiver type:

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

The struct's type parameters and bounds are in scope throughout a nested method. A method may also declare generic parameters of its own after its name. A nested `@where` may further constrain the enclosing receiver family, including for an `@override`, in the same form as an out-of-line method.

The out-of-line spelling prefixes the method name with `S::`, where `S` is the struct it belongs to. Both spellings declare the same method and may be used together, so a struct can present a method's signature while keeping its body elsewhere:

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

A body may be nested or out of line independently for each method. Fields and methods may appear in either order inside the struct.

The first parameter is the receiver and is always a reference. The method therefore acts on the instance and not on a copy.

```
fn S::method(self: &S) {
    // ...
}
```

`&self` is a shorthand that supplies the receiver type. For a [generic struct](#methods-of-a-generic-struct), it also supplies a type such as `&S<A, B>`:

```
fn S::method(&self) {
    // ...
}
```

If the method does not modify the instance, declare the receiver as `self: const &S` or `const &self`. This permits calls on a `const S`. A call to a modifying method with a `const` instance is an error.

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

#### Builder methods

A method that mutates its receiver and returns it can use `self` as its return type:

```
fn String::null_terminate(&self) -> self {
    self.push('\0');
}
```

The return is implicit. A bare `return` or `return self` may be used to leave early. Returning any other value is an error. Only a method with a mutable `&self` receiver can return `self`.

Chaining builder methods does not copy the receiver by itself:

```
s.a().b();

fn S::c(&self) -> &S {
    return self.a().b();
}
```

Both chains keep acting on `s`. A copy is made only when the result is used as a new owned value, as in `let result = s.c();`.

Calling a builder method on a named value mutates that value, then copies the result:

```
let str = String("text");
let str2 = str.null_terminate();
```

Both `str` and `str2` have the same contents and are null-terminated, but they have different buffers. An owned type that implements [`Destroy`](#destruction-and-raii) must also implement [`Clone`](#assignment-and-ownership) to make this copy.

`String::null_terminated` uses the builder method to create a new value:

```
fn String::null_terminated(src: const char*) -> String {
    return String(src).null_terminate();
}
```

Calling the same method on a temporary keeps that temporary as the result:

```
let str = String("text").null_terminate();
```

This is equivalent to constructing `str` first and then calling the method:

```
let str = String("text");
str.null_terminate();
```

#### Constructors

For a struct `S` with an `init` method, `S(args)` creates an instance in place. It allocates stack space and applies the [field defaults](#field-defaults). It then calls `S::init(self, args...)`. This is the expression form of:

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

#### Built-in type methods

Methods may be declared directly on built-in types in the same way as methods on structs:

```
fn i32::doubled(const &self) -> i32 {
    return self * 2;
}

let answer = 21.doubled();
```

Use `@where` with a built-in bound to declare one method for a family of built-in types:

```
@where<T: Scalar>
fn T::value(const &self) -> T {
    return self;
}
```

`Scalar` covers all primitive value types. `Integer`, `SignedInteger`, and `UnsignedInteger` provide narrower built-in families.

Arrays support methods in the same way. Using `T` as the element type declares the method for every array type:

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

[Operator shorthands](#operator-overloading) also use array methods, so `eq` provides `==` and `!=`, while `add` provides `+`.

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

A type implements an interface by listing it after `:` and providing every required field and method. Multiple interfaces are separated by commas, as in `struct Person: Named, Aged`. Required methods may also be declared outside the interface body as `fn Named::greet(&self) -> char[];`.

Interfaces may be used only as parameter types. Calls are compiled for each concrete argument type, with no run-time interface object or dispatch.

#### Generic interfaces

Interfaces can be generic like structs. Their name is followed by `<T>`, and their body can use the type parameters:

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

A claim type argument can be an interface. For example, `struct List<T>: Add<List<T>, Iterable<T>>` requires an `add` method that takes any iterable. An overload with that interface parameter satisfies the requirement.

#### Extending types

`@extend` adds methods and interface claims to an existing type. Methods in the block use that type as their receiver:

```
@extend Number: Formattable {
    fn format(const &self) -> String { ... }
}
```

An interface claim may be written separately when its methods are defined elsewhere:

```
@extend Number: Formattable;

fn Number::format(const &self) -> String { ... }
```

Extensions work with structs, enums, primitives, aliases, and generic type families. A concrete receiver extends only that type; a receiver containing a placeholder, such as `T[]`, extends every matching type.

`@where<T: Bound>` restricts a generic function, method, or extension to types that satisfy `Bound`. Braces apply the same bound to a group, and nested `@where` bounds combine:

```
@where<T: Scalar> {
    @extend T[]: Hashable;
    fn T[]::hash(const &self) -> u64 { ... }
}
```

Extending a primitive makes the claimed interface methods available but does not change the primitive's built-in operators.

#### The iteration interfaces

`Iterator<T>`, `ConstIterator<T>`, and `Iterable<T>` are built-in and available without an import:

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

`Iterator<T>` returns mutable element references, while `ConstIterator<T>` returns read-only references. `Iterable<T>` provides both forms. A `foreach` uses `iterator()` for a mutable value and `const_iterator()` for a `const` value.

[Arrays](#arrays) implement `Iterable<T>` automatically, so they work with `foreach` and may be passed anywhere an `Iterable<T>` is expected. Iterator references point to the original elements, allowing mutable iteration to update the collection.

### Optional values

The built-in `Option<T>` represents a value that may be absent. It contains a `present` tag and a `value` of type `T`; the built-in value `None` creates an absent option. Its type is normally inferred from a return type, annotation, or function parameter, and can be written explicitly as `None<T>` when there is no typed context.

An ordinary `T` implicitly fills an `Option<T>`, so functions can return their value directly:

```
fn find(enabled: bool) -> Option<i32> {
    if (enabled) {
        return 42;
    }
    return None;
}

let missing: Option<i32> = None;
let explicit = None<i32>;
```

An option is truthy when it is present. Compare it with `None`, test it directly, or read its `present` tag. After the check establishes that the option is present, it may decay to `T`; its `value` member may also be read directly:

```
fn use(value: Option<i32>) {
    if (value == None) {
        report_missing();
    } else {
        consume(value);       // checked Option<i32> decays to i32
        consume(value.value); // direct access is valid on this path
    }
}
```

The check remains known after an absent branch leaves through `return`, `break`, or a call to an `@noreturn` function. Assigning to the option or exposing its address invalidates the check.

When `T` implements `Destroy`, `Option<T>` does too. Replacing a present option destroys its previous value, explicitly dropping the option destroys a present value, and normal scope cleanup does the same. Dropping `None` has no contained value to destroy. An `Option<T>` whose `T` does not implement `Destroy` is not owned.

### Error handling

Sie uses the built-in `Result<V, E>` when an operation returns either a value or an error. `Result<E>` represents success without a value. Create results with `Ok` and `Error`; their types are usually inferred from the surrounding return type or annotation.

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

Only one result member is valid at a time. The compiler allows `value` only after a successful result check and `error` only after a failed one:

```
if (res.ok) {
    use(res.value);
} else {
    report(res.error);
}
```

The check also remains known after a branch that returns, breaks, or otherwise leaves. Assigning to the result or exposing its address invalidates what was known, so it must be checked again.

#### Unwrapping with try

`try <result> except (<name>) { ... }` unwraps a successful result. On failure, the `except` block runs with the error bound to `name`:

```
let value = try divide(10, 2) except (error) { return 1; }
let value = try divide(10, 2) except (error) {
    report(error);
    emit 0;
}
```

For `Result<V, E>`, the block must leave the current control flow or use `emit` to provide a replacement value. A `Result<E>` has no value to replace, so its block may finish normally.

#### The fallback shorthand

`try <result> ?? <fallback>` unwraps the result or uses the fallback on error. The fallback is evaluated only when needed:

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

A bare `try` unwraps a successful result or immediately returns its error to the caller:

```
fn read_config(path: char*) -> Result<Config, IOError> {
    try file.open();                 // an IOError here returns from read_config
    let size = try file.size();      // and here, otherwise 'size' is the value

    return Ok(parse(file, size));
}
```

The enclosing function must return a `Result` with the same error type. A bare `try` used as a statement ends with `;`.

## Copyright

Most of the project is licensed under the [BSD 3-Clause License](LICENSE). The [GLib](packages/glib/LICENSE) and [GTK](packages/gtk/LICENSE) packages are instead licensed under the GNU LGPL v2 only.
