"""Parsing of struct declarations."""

from siec.ast import Field, Struct
from siec.lexer.token import int_value
from siec.parser.stream import TokenStream
from siec.parser.types import parse_type


def parse_struct(ts: TokenStream) -> Struct:
    """
    Parse a struct declaration: 'struct Name { a: A; b: B; }', with an optional
    trailing ';', or a bodiless forward declaration 'struct Name;'.

    '@packed', '@align(N)', and '@volatile' decorators may precede the
    keyword, in any order.
    """
    line = ts.peek().line

    packed = False
    align = None
    volatile = False
    while ts.peek().value == "@":
        at_line = ts.peek().line
        ts.next()
        decorator = ts.expect("ident").value

        if decorator == "packed":
            packed = True
        elif decorator == "volatile":
            volatile = True
        elif decorator == "align":
            ts.expect("sym", "(")
            align = int_value(ts.expect("int").value)
            ts.expect("sym", ")")

            if align == 0 or align & (align - 1):
                raise SyntaxError(f"line {at_line}: alignment must be a "
                                  f"power of two, not {align}")
        else:
            raise SyntaxError(f"line {at_line}: unknown struct decorator "
                              f"'@{decorator}'")

    # 'union' declares the same shape with its fields sharing one storage
    is_union = ts.peek().value == "union"
    if is_union:
        if packed:
            raise SyntaxError(f"line {line}: a union has no field layout "
                              "to '@packed'")

        ts.next()
    else:
        ts.expect("kw", "struct")

    name = ts.expect("ident").value

    # '<T, U>' names the type parameters of a generic struct, instantiated
    # by use: 'S<i32>' stamps out a concrete struct per argument list
    params = None
    if ts.peek().syntax == "<":
        ts.next()
        params = [ts.expect("ident").value]
        while ts.peek().syntax == ",":
            ts.next()
            params.append(ts.expect("ident").value)
        ts.expect("sym", ">")

    # a ';' in place of a body is a forward declaration, leaving the fields
    # to a later definition — or to none, for an opaque struct
    if ts.peek().value == ";":
        ts.next()
        return Struct(name, None, packed, align, volatile, is_union,
                      params=params, line=line)

    ts.expect("sym", "{")

    # 'name: type [= default];' fields until the closing brace
    fields = []
    while ts.peek().value != "}":
        # an unnamed 'struct { ... }' or 'union { ... }' member hoists its
        # fields into this type, C-style; '#n' names its own slot
        if ts.peek().value in ("struct", "union") and ts.peek(1).syntax == "{":
            fields.append(Field(f"#{len(fields)}", parse_type(ts)))
            ts.expect("sym", ";")
            continue

        field_name = ts.expect("ident").value
        ts.expect("sym", ":")
        field_type = parse_type(ts)

        default = None
        if ts.peek().syntax == "=":
            from siec.parser.expressions import parse_expression

            ts.next()
            default = parse_expression(ts)

        fields.append(Field(field_name, field_type, default))
        ts.expect("sym", ";")

    ts.expect("sym", "}")

    # an optional ';' may close the declaration, C-style
    if ts.peek().value == ";":
        ts.next()

    return Struct(name, fields, packed, align, volatile, is_union,
                  params=params, line=line)
