"""Parsing of statements."""

from siec.ast import (
    Assign,
    BinaryOp,
    Block,
    Break,
    Call,
    Case,
    Continue,
    Defer,
    Emit,
    ExprStmt,
    For,
    Foreach,
    If,
    Index,
    IndexAssign,
    Let,
    Member,
    MemberAssign,
    MethodCall,
    RefAssign,
    Return,
    Var,
    When,
    While,
)
from siec.parser.expressions import parse_asm_tail, parse_expression
from siec.parser.stream import TokenStream
from siec.parser.types import parse_type

COMPOUND = {"+=", "-=", "*=", "/=", "%=", "**=", "<<=", ">>=", "&=", "|=", "^="}


def parse_block(ts: TokenStream) -> list:
    """
    Parse a brace-enclosed list of statements.
    """
    ts.expect("sym", "{")

    body = []
    while ts.peek().syntax != "}":
        body.append(parse_statement(ts))

    ts.expect("sym", "}")
    return body


def parse_body(ts: TokenStream) -> list:
    """
    Parse a control-flow body: a braced block, or a single braceless
    statement standing in for one.
    """
    if ts.peek().syntax == "{":
        return parse_block(ts)

    return [parse_statement(ts)]


def parse_arm(ts: TokenStream) -> list:
    """
    Parse one case arm's body: statements up to the next 'when', 'else',
    or the closing brace.
    """
    body = []
    while ts.peek().syntax not in ("when", "else", "}"):
        body.append(parse_statement(ts))

    return body


def parse_statement(ts: TokenStream):
    """
    Parse a statement: a let, an if, a return, an assignment, or an expression.
    """
    tok = ts.peek()
    line = tok.line

    # '@asm' embeds an assembly block as a statement, no ';' after its braces
    if tok.syntax == "@" and ts.peek(1).value == "asm":
        ts.next()
        return ExprStmt(parse_asm_tail(ts), line=line)

    # 'if (cond) body' with an optional 'else' body or 'else if' chain
    if tok.kind == "kw" and tok.value == "if":
        ts.next()

        ts.expect("sym", "(")
        condition = parse_expression(ts)
        ts.expect("sym", ")")

        body = parse_body(ts)

        # an 'else' followed by ':' belongs to an enclosing case, not this if
        orelse = None
        if ts.peek().syntax == "else" and ts.peek(1).syntax != ":":
            ts.next()
            orelse = parse_body(ts)

        return If(condition, body, orelse, line=line)

    # 'case (subject) { when v: ... else: ... }' runs exactly one arm
    if tok.kind == "kw" and tok.value == "case":
        ts.next()

        ts.expect("sym", "(")
        subject = parse_expression(ts)
        ts.expect("sym", ")")
        ts.expect("sym", "{")

        arms = []
        orelse = None
        while ts.peek().syntax != "}":
            if ts.peek().syntax == "when":
                ts.next()

                # one or more comma-separated values, any of which matches
                values = [parse_expression(ts)]
                while ts.peek().syntax == ",":
                    ts.next()
                    values.append(parse_expression(ts))

                ts.expect("sym", ":")
                arms.append(When(values, parse_arm(ts)))
            elif ts.peek().syntax == "else":
                ts.next()
                ts.expect("sym", ":")
                orelse = parse_arm(ts)

                # nothing may follow the else arm
                if ts.peek().syntax != "}":
                    raise SyntaxError(f"line {ts.peek().line}: 'else' must be "
                                      "the last arm of a case")
            else:
                raise SyntaxError(f"line {ts.peek().line}: expected 'when' or "
                                  f"'else', got {ts.peek().value!r}")

        ts.expect("sym", "}")
        return Case(subject, arms, orelse, line=line)

    # 'while (cond) body' loops its body while the condition is truthy
    if tok.kind == "kw" and tok.value == "while":
        ts.next()

        ts.expect("sym", "(")
        condition = parse_expression(ts)
        ts.expect("sym", ")")

        return While(condition, parse_body(ts), line=line)

    # 'foreach (v : iterable) body' walks the iterable's elements, 'v'
    # referencing each in turn
    if tok.kind == "kw" and tok.value == "foreach":
        ts.next()

        ts.expect("sym", "(")
        name = ts.expect("ident").value
        ts.expect("sym", ":")
        iterable = parse_expression(ts)
        ts.expect("sym", ")")

        return Foreach(name, iterable, parse_body(ts), line=line)

    # 'for (init; cond; step) body': the init runs once, the condition
    # is checked before each pass, and the step runs after each
    if tok.kind == "kw" and tok.value == "for":
        ts.next()

        ts.expect("sym", "(")
        init = parse_statement(ts)  # consumes its own ';'

        condition = parse_expression(ts)
        ts.expect("sym", ";")

        step = parse_step(ts)
        ts.expect("sym", ")")

        return For(init, condition, step, parse_body(ts), line=line)

    # a bare '{' opens a block statement, a statement list in its own scope
    if tok.syntax == "{":
        return Block(parse_block(ts), line=line)

    # 'let name: type' with an optional '= <expr>' initializer; the type
    # may be omitted when an initializer follows to infer it from
    if tok.kind == "kw" and tok.value == "let":
        ts.next()

        name = ts.expect("ident").value

        var_type = None
        if ts.peek().syntax == ":":
            ts.next()
            var_type = parse_type(ts)

        value = None
        if ts.peek().syntax == "=":
            ts.next()
            value = parse_expression(ts)

        if var_type is None and value is None:
            raise SyntaxError(f"line {line}: 'let {name}' needs a type or an "
                              "initializer to infer it from")

        ts.expect("sym", ";")
        return Let(name, var_type, value, line=line)

    # 'defer <expr>;' or 'defer { ... }' pushes the statement onto the
    # enclosing scope's exit stack
    if tok.kind == "kw" and tok.value == "defer":
        ts.next()

        if ts.peek().syntax == "{":
            return Defer(Block(parse_block(ts), line=line), line=line)

        stmt = parse_step(ts)
        ts.expect("sym", ";")
        return Defer(stmt, line=line)

    # 'emit expr;' produces the enclosing block expression's value
    if tok.kind == "kw" and tok.value == "emit":
        ts.next()

        value = parse_expression(ts)
        ts.expect("sym", ";")
        return Emit(value, line=line)

    # 'break' and 'continue' steer the innermost enclosing loop
    if tok.kind == "kw" and tok.value in ("break", "continue"):
        ts.next()
        ts.expect("sym", ";")
        return Break(line=line) if tok.value == "break" else Continue(line=line)

    # 'return' with an optional value expression before the ';'
    if tok.kind == "kw" and tok.value == "return":
        ts.next()

        value = None
        if ts.peek().syntax != ";":
            value = parse_expression(ts)

        ts.expect("sym", ";")
        return Return(value, line=line)

    # otherwise an assignment or expression statement, closed by its ';'
    stmt = parse_step(ts)
    ts.expect("sym", ";")
    return stmt


def parse_step(ts: TokenStream):
    """
    Parse an assignment or expression without its terminator: the tail of a
    statement, or a for loop's step before the closing ')'.
    """
    line = ts.peek().line

    # an assignment operator after an expression makes it an assignment
    # target, else it's evaluated for its effects
    expr = parse_expression(ts)

    if ts.peek().syntax == "=" or ts.peek().syntax in COMPOUND:
        op = ts.next().value

        value = parse_expression(ts)
        # a compound 'lvalue <op>= v' desugars to 'lvalue = lvalue <op> v'
        if op != "=":
            value = BinaryOp(op[:-1], expr, value)

        return make_assignment(expr, value, line)

    return ExprStmt(expr, line=line)


def make_assignment(target, value, line: int):
    """
    Build the assignment for an lvalue target: a variable, a struct field,
    or an indexed element.
    """
    if isinstance(target, Var):
        return Assign(target.name, value, line=line)

    if isinstance(target, Member):
        return MemberAssign(target.base, target.field, value, line=line)

    if isinstance(target, Index):
        return IndexAssign(target.base, target.index, value, line=line)

    # a call target assigns through the reference it returns
    if isinstance(target, (Call, MethodCall)):
        return RefAssign(target, value, line=line)

    raise SyntaxError(f"line {line}: invalid assignment target")
