"""Flow-sensitive null checks for raw pointer operations."""

from dataclasses import fields, is_dataclass

from siec.ast import (
    Assign,
    BinaryOp,
    Block,
    Break,
    Call,
    Case,
    Cast,
    ClosureExpr,
    CompoundAssign,
    Continue,
    Defer,
    Emit,
    ExprStmt,
    For,
    Foreach,
    If,
    Index,
    Let,
    LetTuple,
    Member,
    MethodCall,
    NullLiteral,
    RefAssign,
    Return,
    Slice,
    Ternary,
    Try,
    UnaryOp,
    Var,
    While,
)
from siec.codegen.errors import source_location, warn
from siec.codegen.generator import Variable
from siec.codegen.inference import expr_sie_type
from siec.codegen.types import is_nonnull_pointer, strip_const, strip_nonnull

UNKNOWN = "unknown"
NULL = "null"
NONNULL = "non-null"


def nullable_pointer(type_name: str | None) -> bool:
    """Whether a type is a nullable raw pointer."""
    name = strip_const(type_name) if type_name is not None else ""
    return name.endswith("*") and not is_nonnull_pointer(name)


def pointer_type(type_name: str | None) -> bool:
    """Whether a type is a nullable or non-null raw pointer."""
    name = strip_const(strip_nonnull(type_name)) if type_name else ""
    return name.endswith("*")


def merge(left: dict, right: dict) -> dict:
    """Keep a null fact only when both continuing paths agree."""
    return {
        name: left.get(name, UNKNOWN)
        if left.get(name, UNKNOWN) == right.get(name, UNKNOWN)
        else UNKNOWN
        for name in left.keys() | right.keys()
    }


class NullFlow:
    """Check non-null requirements and optional dereference warnings."""

    def __init__(self, gen, fn, scope: dict):
        self.gen = gen
        self.fn = fn
        self.types = {
            name: Variable(None, variable.type)
            for name, variable in scope.items()
        }
        self.state = {
            name: NONNULL if is_nonnull_pointer(variable.type) else UNKNOWN
            for name, variable in scope.items()
            if pointer_type(variable.type)
        }
        self.warned = set()
        self.unstable = set()

    def expression_state(self, expr, state: dict) -> str:
        """Return the null state one pointer expression produces."""
        if isinstance(expr, NullLiteral):
            return NULL
        if isinstance(expr, Var):
            return state.get(expr.name, UNKNOWN)
        if isinstance(expr, UnaryOp):
            if expr.op in ("&", "nonnull"):
                return NONNULL
            return UNKNOWN
        if isinstance(expr, Cast):
            return (NONNULL if self.expression_state(expr.operand, state) == NONNULL
                    else UNKNOWN)
        if isinstance(expr, Ternary):
            left = self.expression_state(expr.then, state)
            right = self.expression_state(expr.orelse, state)
            return left if left == right else UNKNOWN

        type_name = expr_sie_type(self.gen, expr, self.types)
        return NONNULL if is_nonnull_pointer(type_name) else UNKNOWN

    def require_nonnull(self, expr, state: dict) -> None:
        """Reject one recorded strengthening without a flow proof."""
        if (getattr(expr, "requires_nonnull", False)
                and self.expression_state(expr, state) != NONNULL):
            shown = f"pointer {expr.name!r}" if isinstance(expr, Var) else "pointer"
            raise TypeError(
                f"{shown} is not definitely non-null; check it against null "
                "or use postfix '!'")

    def pointer_name(self, expr) -> str:
        """Describe a warned pointer expression."""
        return repr(expr.name) if isinstance(expr, Var) else "expression"

    def warn_dereference(self, expr, state: dict, line: int) -> None:
        """Warn once for an unproved nullable pointer dereference."""
        type_name = expr_sie_type(self.gen, expr, self.types)
        if ("unchecked-dereference" not in self.gen.enabled_warnings
                or not nullable_pointer(type_name)
                or self.expression_state(expr, state) == NONNULL
                or id(expr) in self.warned):
            return

        self.warned.add(id(expr))
        warn(
            self.gen,
            f"pointer {self.pointer_name(expr)} is not definitely non-null "
            "when dereferenced",
            line,
            self.fn.file,
            code="unchecked-dereference",
        )

    def visit_expression(self, expr, state: dict, line: int) -> None:
        """Check an expression and apply local postfix assertions."""
        if expr is None:
            return

        if isinstance(expr, ClosureExpr):
            return

        self.require_nonnull(expr, state)

        if isinstance(expr, UnaryOp):
            if expr.op == "nonnull":
                expr.nonnull_proven = (
                    self.expression_state(expr.operand, state) == NONNULL)
                self.visit_expression(expr.operand, state, line)
                if (isinstance(expr.operand, Var)
                        and expr.operand.name not in self.unstable):
                    state[expr.operand.name] = NONNULL
                return
            if (expr.op == "&" and isinstance(expr.operand, Var)
                    and expr.operand.name in state):
                self.unstable.add(expr.operand.name)
                state[expr.operand.name] = UNKNOWN
                return
            if expr.op == "*":
                self.warn_dereference(expr.operand, state, line)
            self.visit_expression(expr.operand, state, line)
            return

        if isinstance(expr, Index):
            if pointer_type(expr_sie_type(self.gen, expr.base, self.types)):
                self.warn_dereference(expr.base, state, line)
            self.visit_expression(expr.base, state, line)
            self.visit_expression(expr.index, state, line)
            return

        if isinstance(expr, BinaryOp):
            self.visit_expression(expr.left, state, line)
            self.visit_expression(expr.right, state, line)
            return

        if isinstance(expr, Ternary):
            yes, no = self.condition(expr.condition, state, line)
            self.visit_expression(expr.then, yes, line)
            self.visit_expression(expr.orelse, no, line)
            state.update(merge(yes, no))
            return

        if isinstance(expr, Try):
            self.visit_expression(expr.result, state, line)
            arm = dict(state)
            self.block(expr.body or [], arm)
            return

        if isinstance(expr, MethodCall):
            self.visit_expression(expr.receiver, state, line)
            for arg in expr.args:
                self.visit_expression(arg, state, line)
            self.invalidate_addresses([expr.receiver, *expr.args], state)
            return

        if isinstance(expr, Call):
            for arg in expr.args:
                self.visit_expression(arg, state, line)
            self.invalidate_addresses(expr.args, state)
            return

        if isinstance(expr, Slice):
            self.visit_expression(expr.base, state, line)
            self.visit_expression(expr.start, state, line)
            self.visit_expression(expr.stop, state, line)
            return

        if is_dataclass(expr):
            for field in fields(expr):
                value = getattr(expr, field.name)
                if is_dataclass(value):
                    self.visit_expression(value, state, line)
                elif isinstance(value, list):
                    for item in value:
                        if is_dataclass(item):
                            self.visit_expression(item, state, line)

    def invalidate_addresses(self, args: list, state: dict) -> None:
        """Forget a pointer local passed by mutable address."""
        for arg in args:
            if (isinstance(arg, UnaryOp) and arg.op == "&"
                    and isinstance(arg.operand, Var)
                    and arg.operand.name in state):
                state[arg.operand.name] = UNKNOWN

    def facts(self, expr) -> tuple[dict, dict]:
        """Return facts established when a condition is true or false."""
        if isinstance(expr, UnaryOp) and expr.op == "not":
            yes, no = self.facts(expr.operand)
            return no, yes

        if (isinstance(expr, Var) and expr.name in self.types
                and pointer_type(self.types[expr.name].type)):
            return {expr.name: NONNULL}, {expr.name: NULL}

        if isinstance(expr, BinaryOp) and expr.op in ("==", "!="):
            for pointer, other in ((expr.left, expr.right),
                                   (expr.right, expr.left)):
                if (isinstance(pointer, Var) and isinstance(other, NullLiteral)
                        and pointer.name in self.types
                        and pointer_type(self.types[pointer.name].type)):
                    yes = {pointer.name: NULL}
                    no = {pointer.name: NONNULL}
                    return (yes, no) if expr.op == "==" else (no, yes)

        return {}, {}

    def condition(self, expr, state: dict, line: int) -> tuple[dict, dict]:
        """Check a condition under short-circuit flow and split its state."""
        if isinstance(expr, UnaryOp) and expr.op == "not":
            yes, no = self.condition(expr.operand, state, line)
            return no, yes

        if isinstance(expr, BinaryOp) and expr.op == "and":
            left_yes, left_no = self.condition(expr.left, state, line)
            right_yes, right_no = self.condition(expr.right, left_yes, line)
            return right_yes, merge(left_no, right_no)

        if isinstance(expr, BinaryOp) and expr.op == "or":
            left_yes, left_no = self.condition(expr.left, state, line)
            right_yes, right_no = self.condition(expr.right, left_no, line)
            return merge(left_yes, right_yes), right_no

        current = dict(state)
        self.visit_expression(expr, current, line)
        yes, no = self.facts(expr)
        return {**current, **yes}, {**current, **no}

    def block(self, statements: list, state: dict | None = None) -> bool:
        """Check statements and return whether control can fall through."""
        if state is None:
            state = self.state
        for stmt in statements:
            with source_location(line=getattr(stmt, "line", 0)):
                if not self.statement(stmt, state):
                    return False
        return True

    def statement(self, stmt, state: dict) -> bool:
        """Check one statement and update its continuing null state."""
        line = getattr(stmt, "line", 0)
        if isinstance(stmt, Let):
            self.visit_expression(stmt.value, state, line)
            self.types[stmt.name] = Variable(None, stmt.type)
            if pointer_type(stmt.type):
                state[stmt.name] = (
                    NONNULL if is_nonnull_pointer(stmt.type)
                    else self.expression_state(stmt.value, state))
            return True

        if isinstance(stmt, LetTuple):
            self.visit_expression(stmt.value, state, line)
            return True

        if isinstance(stmt, Assign) and not stmt.qualified:
            self.visit_expression(stmt.value, state, line)
            if stmt.name in state:
                state[stmt.name] = self.expression_state(stmt.value, state)
            return True

        if isinstance(stmt, If):
            yes, no = self.condition(stmt.condition, state, line)
            yes_falls = self.block(stmt.body, yes)
            no_falls = self.block(stmt.orelse or [], no)
            if yes_falls and no_falls:
                state.update(merge(yes, no))
            elif yes_falls:
                state.update(yes)
            elif no_falls:
                state.update(no)
            else:
                return False
            return True

        if isinstance(stmt, While):
            yes, no = self.condition(stmt.condition, state, line)
            body = dict(yes)
            self.block(stmt.body, body)
            state.update(merge(state, body))
            return True

        if isinstance(stmt, (For, Foreach)):
            if isinstance(stmt, Foreach):
                self.visit_expression(stmt.iterable, state, line)
            else:
                loop = dict(state)
                self.statement(stmt.init, loop)
                yes, _ = self.condition(stmt.condition, loop, line)
                self.block(stmt.body, yes)
                self.statement(stmt.step, yes)
                state.update(merge(state, yes))
                return True
            body = dict(state)
            self.block(stmt.body, body)
            state.update(merge(state, body))
            return True

        if isinstance(stmt, Case):
            self.visit_expression(stmt.subject, state, line)
            paths = []
            for arm in stmt.arms:
                arm_state = dict(state)
                for value in arm.values:
                    self.visit_expression(value, arm_state, line)
                if self.block(arm.body, arm_state):
                    paths.append(arm_state)
            if stmt.orelse is not None:
                other = dict(state)
                if self.block(stmt.orelse, other):
                    paths.append(other)
            else:
                paths.append(dict(state))
            if not paths:
                return False
            merged = paths[0]
            for path in paths[1:]:
                merged = merge(merged, path)
            state.update(merged)
            return True

        if isinstance(stmt, Block):
            inner = dict(state)
            falls = self.block(stmt.body, inner)
            if falls:
                state.update({name: value for name, value in inner.items()
                              if name in state})
            return falls

        if isinstance(stmt, Defer):
            deferred = {
                name: (NONNULL if is_nonnull_pointer(variable.type) else UNKNOWN)
                for name, variable in self.types.items()
                if pointer_type(variable.type)
            }
            self.statement(stmt.stmt, deferred)
            return True

        if isinstance(stmt, Return):
            self.visit_expression(stmt.value, state, line)
            return False

        if isinstance(stmt, (Break, Continue)):
            return False

        if isinstance(stmt, Emit):
            self.visit_expression(stmt.value, state, line)
            return False

        if isinstance(stmt, ExprStmt):
            self.visit_expression(stmt.expr, state, line)
            symbol = getattr(stmt.expr, "resolved_symbol", None)
            return symbol not in self.gen.noreturns

        for name in ("target", "base", "index", "value"):
            self.visit_expression(getattr(stmt, name, None), state, line)
        if isinstance(stmt, (RefAssign, CompoundAssign)):
            target = getattr(stmt, "target", None)
            if isinstance(target, Var) and target.name in state:
                state[target.name] = UNKNOWN
        return True


def check_nulls(gen, fn, scope: dict) -> None:
    """Check one function's non-null contracts and pointer dereferences."""
    if fn.body:
        NullFlow(gen, fn, scope).block(fn.body)
