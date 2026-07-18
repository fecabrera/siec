"""Parsing of function declarations, definitions, and whole programs."""

from siec.ast import Function, Global, Param, Program
from siec.parser.constants import parse_const
from siec.parser.enums import parse_enum
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
    includes = []
    functions = []
    structs = []
    consts = []
    enums = []
    globals_ = []

    # '@' starts an '@include' directive, an '@const' declaration, an
    # '@extern let' global, or a decorated function (e.g. '@extern fn');
    # 'struct' and 'enum' start type declarations; anything else is a function
    while ts.peek().kind != "eof":
        if ts.peek().value == "@" and ts.peek(1).value == "include":
            includes.append(parse_include(ts))
        elif ts.peek().value == "@" and ts.peek(1).value == "const":
            consts.append(parse_const(ts))
        elif (ts.peek().value == "@" and ts.peek(1).value == "extern"
              and ts.peek(2).value == "let"):
            globals_.append(parse_global(ts))
        elif ts.peek().value == "struct":
            structs.append(parse_struct(ts))
        elif ts.peek().value == "enum":
            enums.append(parse_enum(ts))
        else:
            functions.append(parse_function(ts))

    return Program(includes, functions, structs, consts, enums, globals_)


def parse_global(ts: TokenStream) -> Global:
    """
    Parse '@extern let name: T;' — a global whose storage lives outside
    this program, so no initializer is allowed.
    """
    line = ts.peek().line
    ts.expect("sym", "@")
    ts.expect("ident", "extern")
    ts.expect("kw", "let")

    name = ts.expect("ident").value
    ts.expect("sym", ":")
    var_type = parse_type(ts)

    if ts.peek().syntax == "=":
        raise SyntaxError(f"line {line}: extern global {name!r} cannot "
                          "have an initializer")

    ts.expect("sym", ";")
    return Global(name, var_type, line=line)


def parse_function(ts: TokenStream) -> Function:
    """
    Parse a function declaration or definition, including decorators
    ('@extern', '@inline') and varargs.
    """
    is_extern = False
    is_inline = False
    line = ts.peek().line

    if ts.peek().value == "@":
        ts.next()
        decorator = ts.expect("ident").value

        if decorator == "extern":
            is_extern = True
        elif decorator == "inline":
            is_inline = True
        else:
            raise SyntaxError(f"line {line}: unknown decorator '@{decorator}'")

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
                        is_inline, line=line)

    if is_extern:
        raise SyntaxError(f"line {ts.peek().line}: extern function {name!r} cannot have a body")

    # the body: statements between braces
    body = parse_block(ts)

    return Function(name, params, return_type, body, is_extern, var_arg,
                    is_inline, line=line)
