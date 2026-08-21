"""Parsing of struct declarations."""

from siec.ast import Field, Struct
from siec.lexer.token import int_value
from siec.parser.stream import TokenStream
from siec.parser.types import parse_type, parse_type_params


def parse_struct(ts: TokenStream) -> Struct:
    """
    Parse a struct declaration: 'struct Name { a: A; b: B; }', with an optional
    trailing ';', or a bodiless forward declaration 'struct Name;'.

    '@packed', '@align(N)', '@volatile', and '@private' decorators may
    precede the keyword, in any order.
    """
    line = ts.peek().line

    packed = False
    align = None
    volatile = False
    is_private = False
    while ts.peek().value == "@":
        at_line = ts.peek().line
        ts.next()
        decorator = ts.expect("ident").value

        if decorator == "packed":
            packed = True
        elif decorator == "volatile":
            volatile = True
        elif decorator == "private":
            is_private = True
        elif decorator == "align":
            ts.expect("sym", "(")
            literal = ts.expect("int")
            align = int_value(literal.value, literal.line)
            ts.expect("sym", ")")

            if align == 0 or align & (align - 1):
                raise SyntaxError(f"line {at_line}: alignment must be a "
                                  f"power of two, not {align}")
        else:
            raise SyntaxError(f"line {at_line}: unknown struct decorator "
                              f"'@{decorator}'")

    # 'union' declares the same shape with its fields sharing one storage;
    # 'interface' an abstract type, all requirement and no storage
    is_union = ts.peek().value == "union"
    is_interface = ts.peek().value == "interface"
    if is_union:
        if packed:
            raise SyntaxError(f"line {line}: a union has no field layout "
                              "to '@packed'")

        ts.next()
    elif is_interface:
        if packed or align is not None or volatile:
            raise SyntaxError(f"line {line}: an interface has no layout "
                              "to decorate")

        ts.next()
    else:
        ts.expect("kw", "struct")

    name = ts.expect("ident").value

    # '<T, U>' names the type parameters of a generic struct, instantiated
    # by use: 'S<i32>' stamps out a concrete struct per argument list
    params, constraints = parse_type_params(ts)

    # ': I, J<T>' after a struct's name declares the interfaces it
    # implements; there is no inheritance, so ':' always means interfaces
    interfaces = None
    if ts.peek().syntax == ":" and not is_interface:
        ts.next()
        interfaces = [parse_type(ts)]
        while ts.peek().syntax == ",":
            ts.next()
            interfaces.append(parse_type(ts))

    # a ';' in place of a body is a forward declaration, leaving the fields
    # to a later definition - or to none, for an opaque struct; a bodiless
    # interface simply requires no fields
    if ts.peek().value == ";":
        ts.next()
        return Struct(name, [] if is_interface else None, packed, align,
                      volatile, is_union, params=params,
                      constraints=constraints,
                      is_interface=is_interface, interfaces=interfaces,
                      is_private=is_private,
                      line=line)

    ts.expect("sym", "{")

    # Fields and methods share the body. A nested method receives the
    # enclosing type's name, parameters, and bounds; after parsing it is the
    # same top-level 'fn S<T>::m' declaration as the out-of-line spelling.
    fields = []
    actions = []
    while ts.peek().value != "}":
        # An interface declares required actions; a struct or union declares
        # ordinary methods. In either case the enclosing declaration supplies
        # the receiver, so the method name itself stays unqualified.
        if (ts.peek().value == "fn"
                or (ts.peek().value == "@"
                    and not (ts.peek(1).value == "private"
                             and ts.peek(2).kind == "ident"
                             and ts.peek(3).value == ":"))):
            # deferred import: functions and structs are mutually recursive
            from siec.parser.functions import (
                merge_constraints,
                parse_function,
                parse_receiver_template,
            )

            if ts.peek().value == "@" and ts.peek(1).value == "where":
                nested = parse_receiver_template(
                    ts, name, params, constraints)
            else:
                nested = [parse_function(ts, name, params)]

            for method in nested:
                method.receiver_constraints = merge_constraints(
                    method.receiver_constraints, constraints)
                actions.append(method)
            continue

        # an unnamed 'struct { ... }' or 'union { ... }' member hoists its
        # fields into this type, C-style; '#n' names its own slot
        if ts.peek().value in ("struct", "union") and ts.peek(1).syntax == "{":
            line = ts.peek().line
            fields.append(Field(f"#{len(fields)}", parse_type(ts), line=line))
            ts.expect("sym", ";")
            continue

        field_private = False
        while ts.peek().value == "@":
            at_line = ts.peek().line
            ts.next()
            decorator = ts.expect("ident").value
            if decorator == "private":
                field_private = True
            else:
                raise SyntaxError(f"line {at_line}: unknown field decorator "
                                  f"'@{decorator}'")

        named = ts.expect("ident")
        field_name = named.value
        ts.expect("sym", ":")
        field_type = parse_type(ts)

        default = None
        if ts.peek().syntax == "=":
            from siec.parser.expressions import parse_expression

            ts.next()
            default = parse_expression(ts)

        fields.append(Field(field_name, field_type, default,
                            is_private=field_private, line=named.line))
        ts.expect("sym", ";")

    ts.expect("sym", "}")

    # an optional ';' may close the declaration, C-style
    if ts.peek().value == ";":
        ts.next()

    return Struct(name, fields, packed, align, volatile, is_union,
                  params=params, constraints=constraints,
                  is_interface=is_interface,
                  interfaces=interfaces, actions=actions,
                  is_private=is_private, line=line)
