"""Parsing of function declarations, definitions, and whole programs."""

import re

from siec.ast import (
    CompileError,
    CondBlock,
    Extend,
    Function,
    Global,
    Import,
    Param,
    Program,
    StaticAssert,
    TypeAlias,
)
from siec.parser.constants import parse_const, parse_macro
from siec.parser.enums import parse_enum
from siec.parser.expressions import parse_clobbers, parse_expression
from siec.parser.includes import parse_include
from siec.parser.statements import parse_block, parse_pattern
from siec.parser.stream import TokenStream
from siec.parser.structs import parse_struct
from siec.parser.types import parse_type, parse_type_params


def parse_program(ts: TokenStream) -> Program:
    """
    Parse a whole program: a sequence of includes, structs, functions,
    constants, and enums.
    """
    return parse_declarations(ts, top_level=True)


def parse_declarations(ts: TokenStream, top_level: bool = False) -> Program:
    """
    Parse a run of top-level declarations into a Program: the whole file,
    or an '@if' branch's contents up to its closing brace.
    """
    program = Program([], [])

    # '@' starts an '@include' directive, an '@if' block, an '@const'
    # declaration, an '@type' alias, an '@extern let' global, or a
    # decorated function (e.g. '@extern fn'); 'struct' and 'enum' start
    # type declarations; anything else is a function
    while ts.peek().kind != "eof":
        if not top_level and ts.peek().syntax == "}":
            break

        if ts.peek().value == "@" and ts.peek(1).value == "include":
            # a conditional include is fine: the loader evaluates the
            # condition and loads only the chosen branch's files
            program.includes.append(parse_include(ts))
        elif ts.peek().value == "import":
            # like an include, an import joins the program before any
            # condition can be evaluated
            if not top_level:
                raise SyntaxError(f"line {ts.peek().line}: an 'import' "
                                  "cannot be conditional")

            program.imports.append(parse_import(ts))
        elif ts.peek().value == "@" and ts.peek(1).value == "if":
            program.conds.append(parse_cond(ts))
        elif ts.peek().value == "@" and ts.peek(1).value == "const":
            program.consts.append(parse_const(ts))
        elif (ts.peek().value == "@" and ts.peek(1).value == "private"
              and ts.peek(2).value == "@"
              and ts.peek(3).value == "const"):
            ts.next()
            ts.next()
            ts.next()
            program.consts.append(
                parse_const(ts, is_private=True, has_at=False))
        elif ts.peek().value == "@" and ts.peek(1).value == "macro":
            program.consts.append(parse_macro(ts))
        elif (ts.peek().value == "@" and ts.peek(1).value in ("extern", "static", "symbol")
              and declares_global(ts)):
            program.globals.append(parse_global(ts))
        elif ts.peek().value in ("struct", "union", "interface") or (
                ts.peek().value == "@" and declares_struct(ts)):
            struct = parse_struct(ts)
            program.structs.append(struct)

            # Methods nested in a struct, union, or interface body are the
            # top-level receiver declarations they spell.
            program.functions.extend(struct.actions)
        elif ts.peek().value == "enum":
            program.enums.append(parse_enum(ts))
        elif (ts.peek().value == "@" and ts.peek(1).value == "private"
              and ts.peek(2).value == "enum"):
            ts.next()
            ts.next()
            program.enums.append(parse_enum(ts, is_private=True))
        elif ts.peek().value == "@" and ts.peek(1).value == "type":
            program.aliases.append(parse_alias(ts))
        elif ts.peek().value == "@" and ts.peek(1).value == "template":
            merge_declarations(program, parse_template(ts))
        elif ts.peek().value == "@" and ts.peek(1).value == "extend":
            ext = parse_extend(ts)
            program.extends.append(ext)
            program.functions.extend(ext.actions)
        elif ts.peek().value == "@" and ts.peek(1).value == "error":
            program.errors.append(parse_error(ts))
        elif ts.peek().value == "@" and ts.peek(1).value == "static_assert":
            program.asserts.append(parse_static_assert(ts))
        else:
            program.functions.append(parse_function(ts))

    return program


def merge_declarations(program: Program, declarations: Program) -> None:
    """Append one parsed declaration group to its surrounding program."""
    for name in (
        "includes",
        "functions",
        "structs",
        "consts",
        "enums",
        "globals",
        "aliases",
        "conds",
        "imports",
        "extends",
        "errors",
        "asserts",
    ):
        getattr(program, name).extend(getattr(declarations, name))


def parse_template(ts: TokenStream) -> Program:
    """
    Parse a generic declaration environment.

    A braced '@template<T: Bound> { ... }' applies to every extension and
    method inside it; without braces it decorates the one extension or
    method following it.
    """
    line = ts.peek().line
    ts.expect("sym", "@")
    ts.expect("ident", "template")
    params, constraints = parse_type_params(ts)
    if params is None:
        raise SyntaxError(f"line {line}: '@template' needs type parameters")

    if ts.peek().syntax == "{":
        ts.next()
        declarations = parse_declarations(ts)
        ts.expect("sym", "}")
    else:
        declarations = Program([], [])
        if ts.peek().value == "@" and ts.peek(1).value == "extend":
            ext = parse_extend(ts)
            declarations.extends.append(ext)
            declarations.functions.extend(ext.actions)
        else:
            declarations.functions.append(parse_function(ts))

    apply_template_environment(declarations, params, constraints, line)
    return declarations


def apply_template_environment(program: Program, params: list[str],
                               constraints: dict | None, line: int) -> None:
    """Attach one '@template' environment to extensions and callables."""
    unsupported = (
        program.includes or program.structs or program.consts
        or program.enums or program.globals or program.aliases
        or program.conds or program.imports or program.errors
        or program.asserts
    )
    if unsupported:
        raise SyntaxError(f"line {line}: an '@template' block may contain "
                          "only extensions and functions or methods")

    actions = {id(action) for ext in program.extends for action in ext.actions}
    for ext in program.extends:
        if ext.params is not None:
            raise SyntaxError(f"line {ext.line}: an extension inside "
                              "'@template' cannot declare type parameters")

        require_template_receiver(ext.name, params, ext.line)
        ext.params = list(params)
        ext.constraints = dict(constraints or {})
        for action in ext.actions:
            apply_method_template(action, params, constraints)

    for fn in program.functions:
        if id(fn) in actions:
            continue
        if fn.receiver is None:
            function_params = set(fn.type_params or ())
            missing = [param for param in params
                       if param not in function_params]
            if missing:
                shown = ", ".join(repr(param) for param in missing)
                raise SyntaxError(
                    f"line {fn.line}: template parameter {shown} is not "
                    f"declared by generic function {fn.name!r}")

            fn.constraints = merge_constraints(fn.constraints, constraints)
            continue

        apply_decorated_method_template(fn, params, constraints)


def merge_constraints(left: dict | None, right: dict | None) -> dict:
    """Merge bound maps, retaining intersections on the same parameter."""
    merged = dict(left or {})
    for param, bound in (right or {}).items():
        previous = merged.get(param)
        bounds = previous if isinstance(previous, tuple) else (previous,)
        bounds += bound if isinstance(bound, tuple) else (bound,)
        ordered = tuple(sorted(value for value in set(bounds)
                               if value is not None))
        merged[param] = ordered[0] if len(ordered) == 1 else ordered
    return merged


def apply_method_template(fn: Function, params: list[str],
                          constraints: dict | None) -> None:
    """Add one template environment to an already parsed receiver method."""
    receiver_params = list(fn.receiver_params or ())
    require_template_receiver(fn.receiver, params, fn.line, receiver_params)

    # A spelled generic receiver introduces all of its placeholders. The
    # environment may constrain only some of them, as in
    # '@template<K: I> fn Map<K, V>::f'. Keep V in the receiver family.
    if not receiver_params:
        fn.receiver_params = list(params)

    fn.receiver_constraints = merge_constraints(
        fn.receiver_constraints, constraints)


def apply_decorated_method_template(fn: Function, params: list[str],
                                    constraints: dict | None) -> None:
    """Apply decorated bounds to a method's own or receiver parameters.

    A method may introduce generic parameters after its name. Those belong to
    the callable rather than its receiver, including when the method lives in
    an extension block. Any remaining decorated parameters specialize the
    receiver family as before.
    """
    own = set(fn.type_params or ())
    method_params = [param for param in params if param in own]
    receiver_params = [param for param in params if param not in own]

    if method_params:
        method_constraints = {
            param: constraints[param]
            for param in method_params
            if constraints is not None and param in constraints
        }
        fn.constraints = merge_constraints(fn.constraints, method_constraints)

    if receiver_params:
        receiver_constraints = {
            param: constraints[param]
            for param in receiver_params
            if constraints is not None and param in constraints
        }
        apply_method_template(fn, receiver_params, receiver_constraints)


def require_template_receiver(receiver: str, params: list[str],
                              line: int,
                              receiver_params: list[str] | None = None) -> None:
    """Require every environment parameter to occur in its receiver type."""
    declared = set(receiver_params or ())
    missing = [
        param for param in params
        if (param not in declared
            and re.search(
                rf"(?<!\w){re.escape(param)}(?!\w)", receiver) is None)
    ]
    if missing:
        shown = ", ".join(repr(param) for param in missing)
        raise SyntaxError(f"line {line}: template parameter {shown} does "
                          f"not occur in receiver {receiver!r}")


def parse_static_assert(ts: TokenStream) -> StaticAssert:
    """
    Parse an '@static_assert(cond, "message")' directive, the trailing
    ';' optional.
    """
    line = ts.peek().line
    ts.expect("sym", "@")
    ts.expect("ident", "static_assert")
    ts.expect("sym", "(")
    condition = parse_expression(ts)
    ts.expect("sym", ",")
    message = ts.expect("str").value
    ts.expect("sym", ")")

    if ts.peek().syntax == ";":
        ts.next()

    return StaticAssert(condition, message, line=line)


def parse_error(ts: TokenStream) -> CompileError:
    """
    Parse an '@error("message")' directive, the trailing ';' optional.
    """
    line = ts.peek().line
    ts.expect("sym", "@")
    ts.expect("ident", "error")
    ts.expect("sym", "(")
    message = ts.expect("str").value
    ts.expect("sym", ")")

    if ts.peek().syntax == ";":
        ts.next()

    return CompileError(message, line=line)


def parse_extend(ts: TokenStream) -> Extend:
    """
    Parse an '@extend[<T: Bound>] Type[: Iface, ...] { ... }' declaration.
    The interface list may end in a semicolon, or a block may hold methods on
    that receiver under the same type parameters. Without an interface list,
    a method block is required.
    """
    line = ts.peek().line
    ts.expect("sym", "@")
    ts.expect("ident", "extend")

    params, constraints = parse_type_params(ts)
    name = parse_type(ts)

    interfaces = []
    if ts.peek().syntax == ":":
        ts.next()
        interfaces.append(parse_type(ts))
        while ts.peek().syntax == ",":
            ts.next()
            interfaces.append(parse_type(ts))

    if ts.peek().syntax == ";":
        if not interfaces:
            raise SyntaxError(f"line {line}: an extension without interface "
                              "claims needs a method body")
        ts.next()
        return Extend(name, interfaces, params=params,
                      constraints=constraints, line=line)

    actions = parse_method_body(ts, name, params, constraints)
    return Extend(name, interfaces, params=params,
                  constraints=constraints, actions=actions, line=line)


def parse_method_body(ts: TokenStream, receiver: str,
                      receiver_params: list[str] | None = None,
                      receiver_constraints: dict | None = None) -> list:
    """
    Parse methods whose enclosing declaration supplies their receiver.

    Extension blocks use this now; a struct body can reuse the same shape
    when inline struct methods become part of the language.
    """
    ts.expect("sym", "{")
    methods = []
    while ts.peek().syntax != "}":
        if ts.peek().value == "@" and ts.peek(1).value == "template":
            methods.extend(parse_receiver_template(
                ts, receiver, receiver_params, receiver_constraints))
            continue

        method = parse_function(ts, receiver, receiver_params)
        method.receiver_constraints = merge_constraints(
            method.receiver_constraints, receiver_constraints)
        methods.append(method)

    ts.next()
    return methods


def parse_receiver_template(ts: TokenStream, receiver: str,
                            receiver_params: list[str] | None,
                            receiver_constraints: dict | None) -> list:
    """Parse a template decorator or group nested in a receiver body."""
    line = ts.peek().line
    ts.expect("sym", "@")
    ts.expect("ident", "template")
    params, constraints = parse_type_params(ts)
    if params is None:
        raise SyntaxError(f"line {line}: '@template' needs type parameters")

    if ts.peek().syntax == "{":
        methods = parse_method_body(
            ts, receiver, receiver_params, receiver_constraints)
    else:
        method = parse_function(ts, receiver, receiver_params)
        method.receiver_constraints = merge_constraints(
            method.receiver_constraints, receiver_constraints)
        methods = [method]

    for method in methods:
        apply_decorated_method_template(method, params, constraints)
    return methods


def parse_import(ts: TokenStream) -> Import:
    """
    Parse an import: 'import a.b[.c][ as m];' binding a whole module, or
    'import { f [as g][, ...] } from a.b;' binding chosen members.
    """
    line = ts.peek().line
    ts.expect("ident", "import")

    # '{ f [as g], ... } from' picks members, bound unqualified
    members = None
    if ts.peek().syntax == "{":
        ts.next()

        members = []
        while ts.peek().syntax != "}":
            if members:
                ts.expect("sym", ",")

                # a trailing comma may close the list, one member per line
                if ts.peek().syntax == "}":
                    break

            name = ts.expect("ident").value
            binding = name
            if ts.peek().value == "as":
                ts.next()
                binding = ts.expect("ident").value

            members.append((name, binding))
        ts.next()

        ts.expect("ident", "from")

    # the module's dotted path
    path = [ts.expect("ident").value]
    while ts.peek().syntax == ".":
        ts.next()
        path.append(ts.expect("ident").value)

    # 'as m' renames the whole module's binding
    alias = None
    if members is None and ts.peek().value == "as":
        ts.next()
        alias = ts.expect("ident").value

    ts.expect("sym", ";")
    return Import(".".join(path), alias, members, line=line)


def parse_cond(ts: TokenStream) -> CondBlock:
    """
    Parse an '@if (cond) { ... }' block, with an optional '@else { ... }'
    or a chained '@else @if (...)'.
    """
    start = ts.peek()
    line = start.line
    ts.expect("sym", "@")
    ts.expect("kw", "if")

    ts.expect("sym", "(")
    condition = parse_expression(ts)
    ts.expect("sym", ")")

    then_open = ts.expect("sym", "{")
    then = parse_declarations(ts)
    then_close = ts.expect("sym", "}")
    then_span = (then_open.line, then_open.col + 1,
                 then_close.line, then_close.col)

    orelse = None
    orelse_span = None
    end_line = then_close.line
    end_col = then_close.col + len(then_close.value)
    if ts.peek().value == "@" and ts.peek(1).value == "else":
        ts.next()
        ts.next()

        # '@else @if' chains: the else arm holds the next condition alone
        if ts.peek().value == "@" and ts.peek(1).value == "if":
            orelse = Program([], [])
            nested = parse_cond(ts)
            orelse.conds.append(nested)
            orelse_span = nested.span
            end_line, end_col = nested.span[2:]
        else:
            else_open = ts.expect("sym", "{")
            orelse = parse_declarations(ts)
            else_close = ts.expect("sym", "}")
            orelse_span = (else_open.line, else_open.col + 1,
                           else_close.line, else_close.col)
            end_line = else_close.line
            end_col = else_close.col + len(else_close.value)

    span = (start.line, start.col, end_line, end_col)
    return CondBlock(condition, then, orelse, line=line,
                     then_span=then_span, orelse_span=orelse_span, span=span)


def parse_alias(ts: TokenStream) -> TypeAlias:
    """
    Parse a type alias: '@type name = T;'.
    """
    line = ts.peek().line
    ts.expect("sym", "@")
    ts.expect("ident", "type")

    name = ts.expect("ident").value

    # '<T, U>' names the type parameters of a generic alias, instantiated
    # by use: 'cmp<i32>' expands the target with each argument list
    params, constraints = parse_type_params(ts)

    ts.expect("sym", "=")
    target = parse_type(ts)
    ts.expect("sym", ";")

    return TypeAlias(name, target, params=params, constraints=constraints,
                     line=line)


def declares_global(ts: TokenStream) -> bool:
    """
    Whether the '@' decorator run at the cursor leads to a 'let': a global
    declaration, whatever mix of decorators precedes it.
    """
    i = ts.pos
    tokens = ts.tokens

    while i < len(tokens) and tokens[i].value == "@":
        i += 2  # the '@' and the decorator's name

        # skip a parenthesized argument ('@symbol("...")')
        if i < len(tokens) and tokens[i].value == "(":
            while i < len(tokens) and tokens[i].value != ")":
                i += 1

            i += 1

    return i < len(tokens) and tokens[i].value == "let"


def declares_struct(ts: TokenStream) -> bool:
    """
    Whether the decorator run at the cursor leads to a type declaration.

    This distinguishes the shared '@private' decorator on a struct from
    '@private fn', while still allowing it to stack with layout decorators.
    """
    i = ts.pos
    tokens = ts.tokens

    while i < len(tokens) and tokens[i].value == "@":
        i += 2
        if i < len(tokens) and tokens[i].value == "(":
            while i < len(tokens) and tokens[i].value != ")":
                i += 1

            i += 1

    return (i < len(tokens)
            and tokens[i].value in ("struct", "union", "interface"))


def parse_global(ts: TokenStream) -> Global:
    """
    Parse a module-level variable: '@extern let name: T;', whose storage
    lives outside this program and takes no initializer, or '@static let
    name: T [= <value>];', file-local storage defined here. An '@extern'
    global may carry '@symbol("...")' to name the outside symbol.
    """
    line = ts.peek().line

    kind = None
    symbol = None
    while ts.peek().value == "@":
        at_line = ts.peek().line
        ts.next()
        decorator = ts.expect("ident").value

        if decorator in ("extern", "static"):
            kind = decorator
        elif decorator == "symbol":
            ts.expect("sym", "(")
            symbol = ts.expect("str").value
            ts.expect("sym", ")")
        else:
            raise SyntaxError(f"line {at_line}: unknown decorator '@{decorator}' "
                              "for a global")

    if symbol is not None and kind != "extern":
        raise SyntaxError(f"line {line}: '@symbol' requires an '@extern' global")

    ts.expect("kw", "let")

    name = ts.expect("ident").value
    var_type = None
    if ts.peek().syntax == ":":
        ts.next()
        var_type = parse_type(ts)

    if kind == "extern" and var_type is None:
        raise SyntaxError(f"line {line}: extern global {name!r} requires "
                          "an explicit type")

    value = None
    if ts.peek().syntax == "=":
        if kind == "extern":
            raise SyntaxError(f"line {line}: extern global {name!r} cannot "
                              "have an initializer")

        ts.next()
        value = parse_expression(ts)

    if var_type is None and value is None:
        raise SyntaxError(f"line {line}: static global {name!r} needs a type "
                          "or an initializer")

    ts.expect("sym", ";")
    return Global(name, var_type, kind == "static", value, symbol, line=line)


DECORATORS = {
    "extern", "inline", "static", "asm", "noreturn", "private",
    "override",
}



def placeholders(ts: TokenStream) -> tuple[list[str] | None, dict | None]:
    """
    Parse an optional '<T, U: Bound>' generic parameter list.
    """
    return parse_type_params(ts)


def parse_function(ts: TokenStream, receiver: str | None = None,
                   receiver_params: list | None = None) -> Function:
    """
    Parse a function declaration or definition, including decorators
    ('@extern', '@inline', '@static') and varargs.

    A receiver given by the caller declares a method of it: an interface
    body's 'fn m(...)' spells the 'fn I::m(...)' it means.
    """
    line = ts.peek().line

    # decorators may stack ('@static @inline'), except '@extern', whose
    # function has no body for the others to act on - only '@noreturn',
    # which describes the signature, rides along with it; '@symbol("name")'
    # names the module symbol and combines with any of them
    decorators = set()
    symbol = None
    clobbers = []
    deprecated = None
    removed = None
    while ts.peek().value == "@":
        at_line = ts.peek().line
        ts.next()
        decorator = ts.expect("ident").value

        if decorator == "symbol":
            ts.expect("sym", "(")
            symbol = ts.expect("str").value
            ts.expect("sym", ")")
            continue

        if decorator == "clobbers":
            clobbers = parse_clobbers(ts)
            continue

        if decorator == "deprecated":
            ts.expect("sym", "(")
            deprecated = ts.expect("str").value
            ts.expect("sym", ")")
            continue

        if decorator == "remove":
            ts.expect("sym", "(")
            removed = ts.expect("str").value
            ts.expect("sym", ")")
            continue

        if decorator not in DECORATORS:
            raise SyntaxError(f"line {at_line}: unknown decorator '@{decorator}'")

        decorators.add(decorator)

    is_extern = "extern" in decorators
    is_inline = "inline" in decorators
    is_static = "static" in decorators
    is_private = "private" in decorators
    is_asm = "asm" in decorators
    noreturn = "noreturn" in decorators
    is_override = "override" in decorators

    if is_extern and decorators - {"extern", "noreturn"}:
        raise SyntaxError(f"line {line}: '@extern' only combines with '@noreturn'")

    # a static function's symbol is the compiler's to mangle
    if is_static and symbol is not None:
        raise SyntaxError(f"line {line}: '@symbol' cannot combine with '@static'")

    # clobbers describe an assembly body, nothing else
    if clobbers and not is_asm:
        raise SyntaxError(f"line {line}: '@clobbers' requires '@asm'")

    ts.expect("kw", "fn")
    name = ts.expect("ident").value

    # '<T, U>' after the first name is either a generic function's type
    # parameters or, before a '::', a generic struct's placeholders
    params_list, params_constraints = placeholders(ts)

    # 'S::m' declares a method: the name canonicalizes to 'S::m', the
    # receiver rides along, and its own '<X, Y>' may follow the method name
    type_params = None
    constraints = None
    receiver_constraints = None
    if receiver is not None:
        name = f"{receiver}::{name}"
        type_params = params_list
        constraints = params_constraints
    elif (params_list is None and ts.peek().syntax == "["
            and ts.peek(1).syntax == "]" and ts.peek(2).syntax == "::"):
        # 'T[]::m' declares a method of the arrays, the element name a
        # placeholder: it stamps per element type, like a generic struct's
        ts.next()
        ts.next()
        ts.next()
        receiver, receiver_params = f"{name}[]", [name]
        name = f"{receiver}::{ts.expect_name('drop').value}"
        type_params, constraints = placeholders(ts)
    elif ts.peek().syntax == "::":
        ts.next()
        receiver, receiver_params = name, params_list
        receiver_constraints = params_constraints
        name = f"{receiver}::{ts.expect_name('drop').value}"
        type_params, constraints = placeholders(ts)
    else:
        type_params = params_list
        constraints = params_constraints

    if is_extern and (type_params is not None or receiver is not None):
        raise SyntaxError(f"line {line}: an '@extern' function cannot be "
                          "generic or a method: it names one foreign symbol")

    if is_static and receiver is not None:
        raise SyntaxError(f"line {line}: a method cannot be '@static'")

    if is_override and removed is not None:
        raise SyntaxError(f"line {line}: '@override' cannot combine with '@remove'")

    ts.expect("sym", "(")

    # comma-separated 'name: type' params, each with an optional
    # '= default'; a trailing '...' marks varargs
    params = []
    var_arg = False
    variadic = False
    while ts.peek().value != ")":
        if params:
            ts.expect("sym", ",")

        if ts.peek().value == "...":
            ts.next()
            var_arg = True
            break

        # '&self' (or 'const &self') opening a method's parameters is
        # sugar for 'self: &S', the receiver's type spelled for it
        if (receiver is not None and not params
                and (ts.peek().syntax == "&" and ts.peek(1).value == "self"
                     or (ts.peek().value == "const" and ts.peek(1).syntax == "&"
                         and ts.peek(2).value == "self"))):
            prefix = ""
            if ts.peek().value == "const":
                ts.next()
                prefix = "const "

            ts.next()  # the '&'
            ts.next()  # 'self'

            # an array receiver already spells its element: '&T[]'
            expected = receiver
            if (receiver_params is not None
                    and receiver not in receiver_params
                    and "<" not in receiver
                    and not receiver.endswith("[]")):
                expected += f"<{','.join(receiver_params)}>"

            params.append(Param("self", f"{prefix}&{expected}"))
            continue

        param_line = ts.peek().line

        # '(a, b): Tuple<...>' destructures one by-value tuple parameter
        # into named element locals inside the body
        pattern = None
        if ts.peek().syntax == "(":
            pattern = parse_pattern(ts)
            param_name = f"#{len(params)}"
            if ts.peek().value == "...":
                raise SyntaxError(f"line {param_line}: a destructured "
                                  "parameter cannot be variadic")
        else:
            param_name = ts.expect("ident").value

            # 'name...' is sugar for a trailing 'name: const Any[]': extra
            # call arguments pack into this borrowed view, each wrapped
            # as an Any
            if ts.peek().value == "...":
                if is_extern:
                    raise SyntaxError(
                        f"line {param_line}: an '@extern' function "
                        "takes C varargs: a bare '...'")

                ts.next()
                params.append(Param(param_name, "const Any[]"))
                variadic = True
                break

        ts.expect("sym", ":")
        param_type = parse_type(ts)

        default = None
        if ts.peek().syntax == "=":
            ts.next()
            default = parse_expression(ts)
        elif params and params[-1].default is not None:
            # defaults fill a call's omitted trailing arguments, so
            # only the last parameters can carry them
            shown = ("destructured parameter" if pattern is not None
                     else f"parameter {param_name!r}")
            raise SyntaxError(f"line {param_line}: {shown} needs a "
                              "default: it follows a defaulted parameter")

        params.append(Param(param_name, param_type, default, pattern=pattern))

    ts.expect("sym", ")")

    # optional '-> type' return annotation
    return_type = None
    if ts.peek().value == "->":
        ts.next()
        return_type = parse_type(ts)

    # an '@noreturn' function hands nothing back: there is no return to type
    if noreturn and return_type is not None:
        raise SyntaxError(f"line {line}: an '@noreturn' function cannot "
                          "declare a return type")

    # a removed function is a tombstone: the declaration stands so its
    # uses name it, but there is nothing left to define
    if removed is not None and ts.peek().value != ";":
        raise SyntaxError(f"line {ts.peek().line}: a '@remove' function "
                          "cannot have a body")

    options = {
        "is_extern": is_extern,
        "var_arg": var_arg,
        "is_inline": is_inline,
        "is_static": is_static,
        "symbol": symbol,
        "clobbers": clobbers,
        "noreturn": noreturn,
        "type_params": type_params,
        "receiver": receiver,
        "receiver_params": receiver_params,
        "receiver_constraints": receiver_constraints,
        "constraints": constraints,
        "variadic": variadic,
        "deprecated": deprecated,
        "removed": removed,
        "is_private": is_private,
        "is_override": is_override,
        "line": line,
    }

    # an '@asm' function's body is raw assembly, captured whole by the lexer
    if is_asm:
        if ts.peek().kind != "asm":
            raise SyntaxError(f"line {ts.peek().line}: an '@asm' function "
                              "needs an assembly body")

        return Function(name, params, return_type, None,
                        asm=ts.next().value, **options)

    # a ';' instead of a body makes this a forward declaration
    if ts.peek().value == ";":
        ts.next()
        return Function(name, params, return_type, None, **options)

    if is_extern:
        raise SyntaxError(f"line {ts.peek().line}: extern function {name!r} cannot have a body")

    # the body: statements between braces
    body = parse_block(ts)

    return Function(name, params, return_type, body, **options)
