"""Parsing of function declarations, definitions, and whole programs."""

from siec.ast import (
    CondBlock,
    Function,
    Global,
    Import,
    Param,
    Program,
    TypeAlias,
)
from siec.parser.constants import parse_const
from siec.parser.enums import parse_enum
from siec.parser.expressions import parse_clobbers, parse_expression
from siec.parser.includes import parse_include
from siec.parser.statements import parse_block
from siec.parser.stream import TokenStream
from siec.parser.structs import parse_struct
from siec.parser.types import parse_type


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
            # an include joins the program before any condition can be
            # evaluated, so a conditional one would mislead
            if not top_level:
                raise SyntaxError(f"line {ts.peek().line}: an '@include' "
                                  "cannot be conditional")

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
        elif (ts.peek().value == "@" and ts.peek(1).value in ("extern", "static", "symbol")
              and declares_global(ts)):
            program.globals.append(parse_global(ts))
        elif ts.peek().value in ("struct", "union") or (
                ts.peek().value == "@" and ts.peek(1).value in ("packed", "align", "volatile")):
            program.structs.append(parse_struct(ts))
        elif ts.peek().value == "enum":
            program.enums.append(parse_enum(ts))
        elif ts.peek().value == "@" and ts.peek(1).value == "type":
            program.aliases.append(parse_alias(ts))
        else:
            program.functions.append(parse_function(ts))

    return program


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
    line = ts.peek().line
    ts.expect("sym", "@")
    ts.expect("kw", "if")

    ts.expect("sym", "(")
    condition = parse_expression(ts)
    ts.expect("sym", ")")

    ts.expect("sym", "{")
    then = parse_declarations(ts)
    ts.expect("sym", "}")

    orelse = None
    if ts.peek().value == "@" and ts.peek(1).value == "else":
        ts.next()
        ts.next()

        # '@else @if' chains: the else arm holds the next condition alone
        if ts.peek().value == "@" and ts.peek(1).value == "if":
            orelse = Program([], [])
            orelse.conds.append(parse_cond(ts))
        else:
            ts.expect("sym", "{")
            orelse = parse_declarations(ts)
            ts.expect("sym", "}")

    return CondBlock(condition, then, orelse, line=line)


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
    params = None
    if ts.peek().syntax == "<":
        ts.next()
        params = [ts.expect("ident").value]
        while ts.peek().syntax == ",":
            ts.next()
            params.append(ts.expect("ident").value)
        ts.expect("sym", ">")

    ts.expect("sym", "=")
    target = parse_type(ts)
    ts.expect("sym", ";")

    return TypeAlias(name, target, params=params, line=line)


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
    ts.expect("sym", ":")
    var_type = parse_type(ts)

    value = None
    if ts.peek().syntax == "=":
        if kind == "extern":
            raise SyntaxError(f"line {line}: extern global {name!r} cannot "
                              "have an initializer")

        ts.next()
        value = parse_expression(ts)

    ts.expect("sym", ";")
    return Global(name, var_type, kind == "static", value, symbol, line=line)


DECORATORS = {"extern", "inline", "static", "asm"}


def parse_function(ts: TokenStream) -> Function:
    """
    Parse a function declaration or definition, including decorators
    ('@extern', '@inline', '@static') and varargs.
    """
    line = ts.peek().line

    # decorators may stack ('@static @inline'), except '@extern', whose
    # function has no body for the others to act on; '@symbol("name")'
    # names the module symbol and rides along with any of them
    decorators = set()
    symbol = None
    clobbers = []
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

        if decorator not in DECORATORS:
            raise SyntaxError(f"line {at_line}: unknown decorator '@{decorator}'")

        decorators.add(decorator)

    is_extern = "extern" in decorators
    is_inline = "inline" in decorators
    is_static = "static" in decorators
    is_asm = "asm" in decorators

    if is_extern and len(decorators) > 1:
        raise SyntaxError(f"line {line}: '@extern' cannot combine with other decorators")

    # a static function's symbol is the compiler's to mangle
    if is_static and symbol is not None:
        raise SyntaxError(f"line {line}: '@symbol' cannot combine with '@static'")

    # clobbers describe an assembly body, nothing else
    if clobbers and not is_asm:
        raise SyntaxError(f"line {line}: '@clobbers' requires '@asm'")

    ts.expect("kw", "fn")
    name = ts.expect("ident").value

    # '<T, U>' names the type parameters of a generic function,
    # instantiated by its calls: 'f(x)' by inference, 'f<i32>(x)' spelled
    type_params = None
    if ts.peek().syntax == "<":
        if is_extern:
            raise SyntaxError(f"line {line}: an '@extern' function cannot "
                              "be generic: it names one foreign symbol")

        ts.next()
        type_params = [ts.expect("ident").value]
        while ts.peek().syntax == ",":
            ts.next()
            type_params.append(ts.expect("ident").value)
        ts.expect("sym", ">")

    ts.expect("sym", "(")

    # comma-separated 'name: type' params; a trailing '...' marks varargs
    params = []
    var_arg = False
    while ts.peek().value != ")":
        if params:
            ts.expect("sym", ",")

        if ts.peek().value == "...":
            ts.next()
            var_arg = True
            break

        param_name = ts.expect("ident").value
        ts.expect("sym", ":")
        params.append(Param(param_name, parse_type(ts)))

    ts.expect("sym", ")")

    # optional '-> type' return annotation
    return_type = None
    if ts.peek().value == "->":
        ts.next()
        return_type = parse_type(ts)

    # an '@asm' function's body is raw assembly, captured whole by the lexer
    if is_asm:
        if ts.peek().kind != "asm":
            raise SyntaxError(f"line {ts.peek().line}: an '@asm' function "
                              "needs an assembly body")

        return Function(name, params, return_type, None, is_extern, var_arg,
                        is_inline, is_static, symbol, ts.next().value, clobbers,
                        type_params=type_params, line=line)

    # a ';' instead of a body makes this a forward declaration
    if ts.peek().value == ";":
        ts.next()
        return Function(name, params, return_type, None, is_extern, var_arg,
                        is_inline, is_static, symbol, type_params=type_params,
                        line=line)

    if is_extern:
        raise SyntaxError(f"line {ts.peek().line}: extern function {name!r} cannot have a body")

    # the body: statements between braces
    body = parse_block(ts)

    return Function(name, params, return_type, body, is_extern, var_arg,
                    is_inline, is_static, symbol, type_params=type_params,
                    line=line)
