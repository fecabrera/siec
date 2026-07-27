"""Flow checking of 'Result' reads: 'ok' decides which member holds.

A 'Result' holds a value or an error, never both, and its 'ok' tag says
which. Reading the wrong one reads storage nobody wrote, so every read
is checked against what the code has established about the tag where the
read stands: 'value' only where 'ok' is known true, 'error' only where it
is known false, and neither where it was never checked.

The knowledge comes from the conditions the code already writes. An
'if (res.ok)' body knows the tag one way and its else the other; a branch
that leaves - returning, breaking, or calling something '@noreturn' -
hands its knowledge to the statements after the if, which is what makes
the early-out shape work:

    let res = f();
    if (not res.ok) {
        report(res.error);   // 'ok' is false here
        return 1;
    }

    use(res.value);          // the branch left, so 'ok' is true from here

Where two paths meet disagreeing the knowledge is lost, and the tag must
be checked again before either member reads.
"""

import dataclasses

from siec.ast import (
    AggregateLiteral,
    ArrayLiteral,
    Assign,
    BinaryOp,
    Block,
    BlockExpr,
    BoolLiteral,
    Break,
    Call,
    Case,
    Cast,
    CompoundAssign,
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
    LetTuple,
    Member,
    MemberAssign,
    MethodCall,
    RefAssign,
    Return,
    Slice,
    Ternary,
    TupleLiteral,
    Try,
    TypeOf,
    UnaryOp,
    Var,
    While,
)
from siec.codegen.aliases import expand_alias
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator, Variable
from siec.codegen.inference import expr_sie_type, infer_type, result_arms
from siec.codegen.types import sized_array, strip_const, strip_reference

# what a condition established about a result's 'ok' tag; a path with no
# state of its own is unchecked, and either member is out of reach
OK = "ok"
ERR = "error"

# the members 'ok' guards, and the tag itself
GUARDED = ("value", "error")


def unhoist(expr):
    """
    An access with the hops into unnamed members peeled off: emission
    rewrites 'res.value' into 'res.#1.value' to reach through the union
    holding it, and the result is the same storage either way.
    """
    while isinstance(expr, Member) and expr.field.startswith("#"):
        expr = expr.base

    return expr


def path_of(expr) -> tuple | None:
    """
    The storage an expression names, 'a.b.c' as ('a', 'b', 'c'), or None
    for anything reached through a call, an index, or a module binding:
    those name nothing a condition could have spoken about earlier.
    """
    expr = unhoist(expr)

    if isinstance(expr, Var):
        if expr.qualified or expr.type_args is not None:
            return None

        return (expr.name,)

    if isinstance(expr, Member) and (base := path_of(expr.base)) is not None:
        return (*base, expr.field)

    return None


def spell(expr) -> str:
    """
    An expression written back as source, close enough to point an error
    at what it read.
    """
    if isinstance(expr, Var):
        return expr.name

    if isinstance(expr, Member):
        if expr.field.startswith("#"):
            return spell(expr.base)

        return f"{spell(expr.base)}.{expr.field}"

    if isinstance(expr, Call):
        return f"{expr.name}({'...' if expr.args else ''})"

    if isinstance(expr, MethodCall):
        return f"{spell(expr.receiver)}.{expr.method}({'...' if expr.args else ''})"

    if isinstance(expr, Index):
        return f"{spell(expr.base)}[...]"

    if isinstance(expr, UnaryOp):
        return f"{expr.op}{spell(expr.operand)}"

    return "the result"


def written(node) -> set:
    """
    The names a statement writes, itself and everything under it: what a
    loop's next pass may have changed since its condition last spoke.

    A 'let' is left out - it names storage of its own, not the storage
    around it - while taking an address counts, since whatever receives
    it may write through it.
    """
    names = set()

    def walk(node) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return

        if not dataclasses.is_dataclass(node):
            return

        if isinstance(node, Assign) and not node.qualified:
            names.add(node.name)
        elif isinstance(node, (MemberAssign, IndexAssign)):
            if (path := path_of(node.base)) is not None:
                names.add(path[0])
        elif isinstance(node, CompoundAssign):
            if (path := path_of(node.target)) is not None:
                names.add(path[0])
        elif isinstance(node, UnaryOp) and node.op == "&":
            if (path := path_of(node.operand)) is not None:
                names.add(path[0])

        for field in dataclasses.fields(node):
            walk(getattr(node, field.name))

    walk(node)
    return names


def escapes(node) -> bool:
    """
    Whether a loop body holds a 'break' of its own: a way out that leaves
    without the loop's condition ever having turned false. A nested loop
    catches its own breaks, so it is not looked into.
    """
    if isinstance(node, list):
        return any(escapes(item) for item in node)

    if not dataclasses.is_dataclass(node):
        return False

    if isinstance(node, Break):
        return True

    if isinstance(node, (While, For, Foreach)):
        return False

    return any(escapes(getattr(node, field.name))
               for field in dataclasses.fields(node))


def bool_values(values: list) -> set | None:
    """
    The truths a case arm matches, or None when it matches anything else:
    only an arm written entirely out of 'true' and 'false' says something
    about a tag.
    """
    if not values or not all(isinstance(value, BoolLiteral) for value in values):
        return None

    return {value.value for value in values}


def tagged(taken: set, yes: dict, no: dict) -> dict:
    """
    What matching a set of truths establishes: one truth carries the
    condition's knowledge that way, both or neither carry nothing.
    """
    if taken == {True}:
        return yes

    if taken == {False}:
        return no

    return {}


def merge(left: dict, right: dict) -> dict:
    """
    What two meeting paths agree on: a tag both sides know the same way
    survives, anything they disagree about goes back to unchecked.
    """
    return {path: state for path, state in left.items()
            if right.get(path) == state}


class ResultFlow:
    """
    A walk over one function body, carrying what is known about every
    result in scope.

    'types' maps each name in scope to its declared type, the way codegen
    does, so the walk can tell a result from any other struct; 'state'
    maps the storage paths whose tag a condition established to what it
    established. Both are undone by the block that introduced them.
    """

    def __init__(self, gen: CodeGenerator, scope: dict):
        self.gen = gen
        self.types = dict(scope)
        self.state: dict = {}
        self.frames: list = []

    # scope

    def declare(self, name: str, type_name: str | None) -> None:
        """
        Bind a name in the current block, remembering what it shadowed so
        the block can hand the outer name back on its way out.
        """
        if self.frames:
            shadowed = {path: state for path, state in self.state.items()
                        if path[0] == name}
            self.frames[-1].append((name, self.types.get(name), shadowed))

        self.forget(name)
        self.types[name] = Variable(None, type_name or "")

    def forget(self, name: str) -> None:
        """
        Drop everything known about a name: the storage it holds changed,
        so whatever a condition said about its tag no longer answers.
        """
        for path in [path for path in self.state if path[0] == name]:
            del self.state[path]

    def unwind(self, frame: list) -> None:
        """
        End a block: every name it declared gives way to what it shadowed.
        """
        for name, shadowed, states in reversed(frame):
            self.forget(name)
            if shadowed is None:
                self.types.pop(name, None)
            else:
                self.types[name] = shadowed

            self.state.update(states)

    # types

    def sie_type(self, expr) -> str | None:
        """
        An expression's Sie type, or None where it has no fixed one.

        The body already compiled, so a type this cannot name is one the
        walk has no business judging, not an error of its own.
        """
        try:
            return expand_alias(self.gen, expr_sie_type(self.gen, expr, self.types))
        except Exception:
            return None

    def result_type(self, expr) -> str | None:
        """
        The 'Result' an expression holds, or None for anything else.
        """
        name = strip_const(strip_reference(strip_const(self.sie_type(expr)) or ""))
        return name if name.startswith("Result<") else None

    def origin(self, expr) -> str | None:
        """
        What a result-valued expression says about its own tag: 'Ok' and
        'Error' build one each way, and a copy of another result carries
        that result's state along.
        """
        if isinstance(expr, Call) and expr.name in ("Ok", "Error"):
            return OK if expr.name == "Ok" else ERR

        if (path := path_of(expr)) is not None:
            return self.state.get(path)

        return None

    def rebind(self, path: tuple, value) -> None:
        """
        Record what a write left behind: the storage is a result again,
        known only as far as the value it took says.
        """
        # what the value says is read before the write forgets the old
        state = self.origin(value)
        self.forget(path[0])
        if state is not None:
            self.state[path] = state

    # statements

    def block(self, stmts: list) -> bool:
        """
        Walk a block, returning whether control falls off its end.
        """
        self.frames.append([])
        try:
            for stmt in stmts:
                if not self.statement(stmt):
                    return False

            return True
        finally:
            self.unwind(self.frames.pop())

    def branch(self, stmts: list, state: dict) -> tuple[dict, bool]:
        """
        Walk one arm of a decision from a state of its own, handing back
        what it knew at its end and whether it got there.
        """
        outer, self.state = self.state, state
        try:
            # the arm's own statements decide what it ends knowing, so
            # its end state is read after they have all run
            falls = self.block(stmts)
            return self.state, falls
        finally:
            self.state = outer

    def statement(self, stmt) -> bool:
        """
        Walk a statement, returning whether control falls out of it.
        """
        with source_location(line=getattr(stmt, "line", 0)):
            return self.statement_body(stmt)

    def statement_body(self, stmt) -> bool:
        if isinstance(stmt, Let):
            if stmt.value is not None:
                self.check(stmt.value)

            type_name = stmt.type
            if type_name is None and stmt.value is not None:
                try:
                    type_name = infer_type(self.gen, stmt.value, self.types)
                except Exception:
                    type_name = None

            # a sized array 'X[N]' declares an 'X[]' over its backing
            if (sized := sized_array(type_name)) is not None:
                type_name = sized[0]

            self.declare(stmt.name, type_name)
            if stmt.value is not None and self.result_type(Var(stmt.name)):
                self.rebind((stmt.name,), stmt.value)
        elif isinstance(stmt, LetTuple):
            self.check(stmt.value)
            for name in flatten(stmt.pattern):
                self.declare(name, None)
        elif isinstance(stmt, Assign):
            self.check(stmt.value)
            if not stmt.qualified and self.result_type(Var(stmt.name)):
                self.rebind((stmt.name,), stmt.value)
            elif not stmt.qualified:
                self.forget(stmt.name)
        elif isinstance(stmt, MemberAssign):
            self.check(stmt.value)
            self.check(stmt.base)
            self.member_assign(stmt)
        elif isinstance(stmt, CompoundAssign):
            self.check(stmt.target)
            self.check(stmt.value)
            if (path := path_of(stmt.target)) is not None:
                self.forget(path[0])
        elif isinstance(stmt, IndexAssign):
            self.check(stmt.base)
            self.check(stmt.index)
            self.check(stmt.value)
        elif isinstance(stmt, RefAssign):
            self.check(stmt.target)
            self.check(stmt.value)
        elif isinstance(stmt, If):
            return self.check_if(stmt)
        elif isinstance(stmt, Case):
            return self.check_case(stmt)
        elif isinstance(stmt, While):
            self.check_loop(stmt.condition, stmt.body, [stmt.body])
        elif isinstance(stmt, For):
            return self.check_for(stmt)
        elif isinstance(stmt, Foreach):
            self.check_foreach(stmt)
        elif isinstance(stmt, Block):
            return self.block(stmt.body)
        elif isinstance(stmt, Defer):
            # the statement runs on the way out, where what is known now
            # no longer holds; it reads from here and leaves nothing
            state = dict(self.state)
            self.branch([stmt.stmt], state)
        elif isinstance(stmt, Return):
            if stmt.value is not None:
                self.check(stmt.value)

            return False
        elif isinstance(stmt, Emit):
            self.check(stmt.value)
            return False
        elif isinstance(stmt, (Break, Continue)):
            return False
        elif isinstance(stmt, ExprStmt):
            self.check(stmt.expr)

            # a call that never gives control back ends the path, exactly
            # like a return does
            return not self.leaves(stmt.expr)

        return True

    def member_assign(self, stmt) -> None:
        """
        Record what writing a field left behind: setting a tag to a
        literal establishes it, setting it to anything else clears what
        was known, and overwriting a whole result rebinds it.
        """
        if (path := path_of(Member(stmt.base, stmt.field))) is None:
            return

        if self.result_type(Member(stmt.base, stmt.field)):
            self.rebind(path, stmt.value)
            return

        if stmt.field != "ok" or not self.result_type(stmt.base):
            return

        self.state.pop(path[:-1], None)
        if isinstance(stmt.value, BoolLiteral):
            self.state[path[:-1]] = OK if stmt.value.value else ERR

    def check_if(self, stmt) -> bool:
        """
        Walk both arms from what the condition established each way, and
        continue from whichever of them control can reach.
        """
        yes, no = self.visit(stmt.condition)

        then_state, then_falls = self.branch(stmt.body, {**self.state, **yes})
        else_state, else_falls = self.branch(stmt.orelse or [],
                                             {**self.state, **no})

        if then_falls and else_falls:
            self.state = merge(then_state, else_state)
        elif then_falls:
            self.state = then_state
        elif else_falls:
            self.state = else_state
        else:
            return False

        return True

    def check_case(self, stmt) -> bool:
        """
        Walk every arm from the state the case began in and continue from
        what the arms control can leave through agree on.

        A case over a tag arms itself on it: 'when true' knows the same
        as an if's body, 'when false' the same as its else, and whatever
        is left over goes to the else arm.
        """
        yes, no = self.visit(stmt.subject)

        entry, ends, matched = dict(self.state), [], set()
        for arm in stmt.arms:
            for value in arm.values:
                self.check(value)

            taken = bool_values(arm.values)
            if taken is not None:
                matched |= taken

            facts = {} if taken is None else tagged(taken, yes, no)
            state, falls = self.branch(arm.body, {**entry, **facts})
            if falls:
                ends.append(state)

        # the else arm runs where no arm matched, which for a tag is
        # exactly the values the arms left out
        rest = tagged({True, False} - matched, yes, no)

        if stmt.orelse is not None:
            state, falls = self.branch(stmt.orelse, {**entry, **rest})
            if falls:
                ends.append(state)
        else:
            # with no else arm, matching nothing walks straight past
            ends.append({**entry, **rest})

        if not ends:
            return False

        state = ends[0]
        for other in ends[1:]:
            state = merge(state, other)

        self.state = state
        return True

    def check_loop(self, condition, body: list, changing: list) -> None:
        """
        Walk a loop: whatever its passes write is unknown at the top,
        since the condition speaks before each of them, and the way out
        is the condition turning false - unless a 'break' jumps it.
        """
        for name in written(changing):
            self.forget(name)

        yes, no = self.visit(condition)
        self.branch(body, {**self.state, **yes})

        if not escapes(body):
            self.state = {**self.state, **no}

    def check_for(self, stmt) -> bool:
        """
        Walk a for loop: the init declares in a scope of its own, ending
        with the loop, and the step joins the body as what each pass may
        change.
        """
        self.frames.append([])
        try:
            self.statement(stmt.init)
            self.check_loop(stmt.condition, stmt.body, [stmt.body, stmt.step])
        finally:
            self.unwind(self.frames.pop())

        return True

    def check_foreach(self, stmt) -> None:
        """
        Walk a foreach: the element binds for the body alone, and the
        loop leaves through its iterator, saying nothing about any tag.
        """
        self.check(stmt.iterable)

        for name in written([stmt.body]):
            self.forget(name)

        source = strip_const(strip_reference(
            strip_const(self.sie_type(stmt.iterable)) or "") or "")
        element = source[:-2] if source.endswith("[]") else ""

        self.frames.append([])
        try:
            self.declare(stmt.name, element)
            self.branch(stmt.body, dict(self.state))
        finally:
            self.unwind(self.frames.pop())

    def leaves(self, expr) -> bool:
        """
        Whether a call never gives control back: an '@noreturn' function
        ends its path the way a return does.
        """
        from siec.codegen.overloads import overload_candidates

        if not isinstance(expr, Call):
            return False

        try:
            symbol = self.gen.resolve_symbol(expr.name)
            candidates = overload_candidates(self.gen, symbol)
        except Exception:
            return False

        return bool(candidates) and all(candidate in self.gen.noreturns
                                        for candidate in candidates)

    # expressions

    def check(self, expr) -> None:
        """
        Walk an expression for reads it isn't allowed to make.
        """
        self.visit(expr)

    def under(self, facts: dict, expr) -> tuple[dict, dict]:
        """
        Visit an expression that only runs where some tags are known:
        the right side of an 'and', a ternary arm, and their like.
        """
        if not facts:
            return self.visit(expr)

        shadowed = {path: self.state.get(path) for path in facts}
        self.state.update(facts)
        try:
            return self.visit(expr)
        finally:
            for path, state in shadowed.items():
                if state is None:
                    self.state.pop(path, None)
                else:
                    self.state[path] = state

    def visit(self, expr) -> tuple[dict, dict]:
        """
        Walk an expression, checking its reads and handing back what it
        establishes about any tag when it holds and when it doesn't.
        """
        if isinstance(expr, UnaryOp):
            if expr.op == "not":
                yes, no = self.visit(expr.operand)
                return no, yes

            self.visit(expr.operand)

            # handing out an address hands out the right to write it
            if expr.op == "&" and (path := path_of(expr.operand)) is not None:
                self.forget(path[0])

            return {}, {}

        if isinstance(expr, BinaryOp):
            return self.visit_binary(expr)

        if isinstance(expr, Ternary):
            yes, no = self.visit(expr.condition)
            self.under(yes, expr.then)
            self.under(no, expr.orelse)
            return {}, {}

        if isinstance(expr, Member):
            return self.visit_member(expr)

        if isinstance(expr, Try):
            return self.visit_try(expr)

        if isinstance(expr, Call):
            for arg in expr.args:
                self.check(arg)
        elif isinstance(expr, MethodCall):
            self.check(expr.receiver)
            for arg in expr.args:
                self.check(arg)
        elif isinstance(expr, Index):
            self.check(expr.base)
            self.check(expr.index)
        elif isinstance(expr, Slice):
            self.check(expr.base)
            for bound in (expr.start, expr.stop):
                if bound is not None:
                    self.check(bound)
        elif isinstance(expr, Cast):
            self.check(expr.operand)
        elif isinstance(expr, (AggregateLiteral, ArrayLiteral, TupleLiteral)):
            for element in expr.elements:
                self.check(element)
        elif isinstance(expr, TypeOf):
            self.check(expr.value)
        elif isinstance(expr, BlockExpr):
            self.block(expr.body)

        return {}, {}

    def visit_binary(self, expr) -> tuple[dict, dict]:
        """
        Walk a binary operation. 'and' and 'or' only run their right side
        where the left let it through, so it is checked knowing that, and
        a comparison against 'true' or 'false' reads as the test itself.
        """
        if expr.op in ("and", "or"):
            yes, no = self.visit(expr.left)
            right_yes, right_no = self.under(yes if expr.op == "and" else no,
                                             expr.right)

            # only the side that ran says anything: an 'and' that failed
            # doesn't say which half did
            if expr.op == "and":
                return {**yes, **right_yes}, {}

            return {}, {**no, **right_no}

        if expr.op in ("==", "!="):
            for side, other in ((expr.left, expr.right), (expr.right, expr.left)):
                if isinstance(other, BoolLiteral):
                    yes, no = self.visit(side)
                    if (expr.op == "==") != other.value:
                        return no, yes

                    return yes, no

        self.visit(expr.left)
        self.visit(expr.right)
        return {}, {}

    def visit_try(self, expr) -> tuple[dict, dict]:
        """
        Walk a 'try': it does its own checking, taking the value only
        where the tag holds, so all that is left is its arm, where the
        error binds to the name it asked for.
        """
        self.check(expr.call)

        arms = result_arms(self.sie_type(expr.call))

        self.frames.append([])
        try:
            self.declare(expr.name, arms[1] if arms is not None else None)
            self.branch(expr.body, dict(self.state))
        finally:
            self.unwind(self.frames.pop())

        return {}, {}

    def visit_member(self, expr) -> tuple[dict, dict]:
        """
        Walk a member read: a result's guarded members must stand where
        the tag says they hold, and reading the tag itself is what
        establishes that for everything after.
        """
        base = unhoist(expr.base)
        spelling = self.result_type(base)
        if spelling is not None and expr.field in GUARDED:
            self.confirm(expr, base)

        self.visit(expr.base)

        if spelling is not None and expr.field == "ok":
            if (path := path_of(base)) is not None:
                return {path: OK}, {path: ERR}

        return {}, {}

    def confirm(self, expr, base) -> None:
        """
        Confirm a guarded read against what is known about the tag, or
        say precisely what stands in its way.
        """
        path = path_of(base)
        state = self.state.get(path) if path is not None else None
        shown, tag = spell(expr), f"{spell(base)}.ok"

        if state is None:
            if path is None:
                raise TypeError(f"cannot read {shown!r}: the result is "
                                "unchecked; name it and check its 'ok' first")

            holds = "an error" if expr.field == "value" else "a value"
            raise TypeError(f"cannot read {shown!r}: {tag!r} is unchecked, "
                            f"so the result may hold {holds}")

        if state == OK and expr.field == "error":
            raise TypeError(f"cannot read {shown!r}: {tag!r} is true here, "
                            "so the result holds a value")

        if state == ERR and expr.field == "value":
            raise TypeError(f"cannot read {shown!r}: {tag!r} is false here, "
                            "so the result holds an error")


def flatten(pattern: list) -> list:
    """
    Every name a destructuring pattern binds, nesting included.
    """
    names = []
    for part in pattern:
        names.extend(flatten(part) if isinstance(part, list) else [part])

    return names


def check_results(gen: CodeGenerator, fn, scope: dict) -> None:
    """
    Check a function body's reads of result members against what its own
    conditions establish about each result's 'ok' tag.

    The body has already been emitted, so every type it names is known
    and every generic arm has been stamped out: what is left is the one
    question the emission never asks, which member is allowed to read.
    """
    if not fn.body:
        return

    ResultFlow(gen, scope).block(fn.body)
