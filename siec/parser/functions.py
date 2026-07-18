"""Parsing of function declarations, definitions, and whole programs."""

from siec.ast import CondBlock, Function, Global, Param, Program, TypeAlias
from siec.parser.constants import parse_const
from siec.parser.enums import parse_enum
from siec.parser.expressions import parse_expression
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
    # declaration, an '@extern let' global, or a decorated function (e.g.
    # '@extern fn'); 'struct', 'enum', and 'type' start type declarations;
    # anything else is a function
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
        elif ts.peek().value == "@" and ts.peek(1).value == "if":
            program.conds.append(parse_cond(ts))
        elif ts.peek().value == "@" and ts.peek(1).value == "const":
            program.consts.append(parse_const(ts))
        elif (ts.peek().value == "@" and ts.peek(1).value in ("extern", "static")
              and ts.peek(2).value == "let"):
            program.globals.append(parse_global(ts))
        elif ts.peek().value == "struct" or (
                ts.peek().value == "@" and ts.peek(1).value in ("packed", "align", "volatile")):
            program.structs.append(parse_struct(ts))
        elif ts.peek().value == "enum":
            program.enums.append(parse_enum(ts))
        elif ts.peek().value == "type":
            program.aliases.append(parse_alias(ts))
        else:
            program.functions.append(parse_function(ts))

    return program


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
    Parse a type alias: 'type name = T;'.
    """
    line = ts.peek().line
    ts.expect("kw", "type")

    name = ts.expect("ident").value
    ts.expect("sym", "=")
    target = parse_type(ts)
    ts.expect("sym", ";")

    return TypeAlias(name, target, line=line)


def parse_global(ts: TokenStream) -> Global:
    """
    Parse a module-level variable: '@extern let name: T;', whose storage
    lives outside this program and takes no initializer, or '@static let
    name: T [= <value>];', file-local storage defined here.
    """
    line = ts.peek().line
    ts.expect("sym", "@")
    kind = ts.expect("ident").value
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
    return Global(name, var_type, kind == "static", value, line=line)


DECORATORS = {"extern", "inline", "static"}


def parse_function(ts: TokenStream) -> Function:
    """
    Parse a function declaration or definition, including decorators
    ('@extern', '@inline', '@static') and varargs.
    """
    line = ts.peek().line

    # decorators may stack ('@static @inline'), except '@extern', whose
    # function has no body for the others to act on
    decorators = set()
    while ts.peek().value == "@":
        at_line = ts.peek().line
        ts.next()
        decorator = ts.expect("ident").value

        if decorator not in DECORATORS:
            raise SyntaxError(f"line {at_line}: unknown decorator '@{decorator}'")

        decorators.add(decorator)

    is_extern = "extern" in decorators
    is_inline = "inline" in decorators
    is_static = "static" in decorators

    if is_extern and len(decorators) > 1:
        raise SyntaxError(f"line {line}: '@extern' cannot combine with other decorators")

    ts.expect("kw", "fn")
    name = ts.expect("ident").value
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

    # a ';' instead of a body makes this a forward declaration
    if ts.peek().value == ";":
        ts.next()
        return Function(name, params, return_type, None, is_extern, var_arg,
                        is_inline, is_static, line=line)

    if is_extern:
        raise SyntaxError(f"line {ts.peek().line}: extern function {name!r} cannot have a body")

    # the body: statements between braces
    body = parse_block(ts)

    return Function(name, params, return_type, body, is_extern, var_arg,
                    is_inline, is_static, line=line)
