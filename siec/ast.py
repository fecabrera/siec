"""AST node definitions for the Sie language."""

from dataclasses import dataclass, field


@dataclass
class IntLiteral:
    """
    An integer literal expression.
    """
    value: int


@dataclass
class FloatLiteral:
    """
    A floating-point literal expression.
    """
    value: float


@dataclass
class StrLiteral:
    """
    A string literal expression.
    """
    value: str


@dataclass
class BoolLiteral:
    """
    A boolean literal expression: 'true' or 'false'.
    """
    value: bool


@dataclass
class Var:
    """
    A reference to a variable by name.
    """
    name: str


@dataclass
class EnumMember:
    """
    An enum member picked through 'A::member': a named compile-time constant.
    """
    enum: str
    member: str


@dataclass
class Call:
    """
    A call to a function by name with a list of argument expressions.
    """
    name: str
    args: list


@dataclass
class Index:
    """
    An indexing expression: a base expression subscripted by an index.
    """
    base: "Expr"
    index: "Expr"


@dataclass
class Slice:
    """
    A slicing expression 'base[start:stop]', with either bound optional:
    'start' defaults to 0 and 'stop' to the base's length.
    """
    base: "Expr"
    start: "Expr | None"
    stop: "Expr | None"


@dataclass
class Member:
    """
    A member access: a field selected from a struct-valued base expression.
    """
    base: "Expr"
    field: str


@dataclass
class AggregateLiteral:
    """
    An aggregate literal '{a, b, ...}' filling a struct or array's fields in order.
    """
    elements: list


@dataclass
class BlockExpr:
    """
    A block used as a value, producing it through an 'emit' statement.
    """
    body: list


@dataclass
class ArrayLiteral:
    """
    An array literal '[a, b, ...]', building a fat array from its elements.
    """
    elements: list


@dataclass
class Cast:
    """
    An explicit conversion of an expression to a named type: 'expr as T'.
    """
    operand: "Expr"
    type: str


@dataclass
class UnaryOp:
    """
    A unary operation applying a prefix operator to one subexpression.
    """
    op: str
    operand: "Expr"


@dataclass
class BinaryOp:
    """
    A binary operation applying an operator to two subexpressions.
    """
    op: str
    left: "Expr"
    right: "Expr"


Expr = (IntLiteral | FloatLiteral | StrLiteral | BoolLiteral | AggregateLiteral | BlockExpr
        | ArrayLiteral | Var | EnumMember | Call | Index | Slice | Member | Cast
        | UnaryOp | BinaryOp)


def _line():
    """
    A source-line field for error reporting, kept out of equality so tests can
    compare nodes without pinning line numbers.
    """
    return field(default=0, compare=False, repr=False)


def _file():
    """
    A source-file field for error reporting, tagged by the loader and kept out
    of equality so tests can compare nodes without pinning file paths.
    """
    return field(default="", compare=False, repr=False)


@dataclass
class Return:
    """
    A return statement with an optional value expression.
    """
    value: Expr | None
    line: int = _line()


@dataclass
class Let:
    """
    A variable declaration with its type and an optional initializer.
    The type may be omitted (None) when an initializer infers it.
    """
    name: str
    type: str | None
    value: Expr | None
    line: int = _line()


@dataclass
class If:
    """
    An if statement with a condition, a body, and an optional else block.
    """
    condition: Expr
    body: list
    orelse: list | None = None
    line: int = _line()


@dataclass
class While:
    """
    A while loop with a condition and a body.
    """
    condition: Expr
    body: list
    line: int = _line()


@dataclass
class For:
    """
    A for loop: an init statement, a condition, and a step statement
    driving the body.
    """
    init: "Stmt"
    condition: Expr
    step: "Stmt"
    body: list
    line: int = _line()


@dataclass
class Block:
    """
    A brace-enclosed statement list run in its own scope.
    """
    body: list
    line: int = _line()


@dataclass
class Assign:
    """
    An assignment of a new value to an existing variable.
    """
    name: str
    value: Expr
    line: int = _line()


@dataclass
class MemberAssign:
    """
    An assignment of a new value to a struct field selected from a base expression.
    """
    base: Expr
    field: str
    value: Expr
    line: int = _line()


@dataclass
class IndexAssign:
    """
    An assignment of a new value to an element indexed from a base expression.
    """
    base: Expr
    index: Expr
    value: Expr
    line: int = _line()


@dataclass
class Emit:
    """
    An 'emit' statement: produces the enclosing block expression's value
    and ends the block.
    """
    value: Expr
    line: int = _line()


@dataclass
class Defer:
    """
    A 'defer' statement: pushes a statement onto the enclosing scope's exit
    stack, run when the scope ends, last deferred first.
    """
    stmt: "Stmt"
    line: int = _line()


@dataclass
class ExprStmt:
    """
    An expression evaluated as a statement, its result discarded.
    """
    expr: Expr
    line: int = _line()


@dataclass
class Param:
    """
    A function parameter with its name and type annotation.
    """
    name: str
    type: str


@dataclass
class Function:
    """
    A function declaration or definition.
    """
    name: str
    params: list[Param]
    return_type: str | None
    body: list | None  # None for declarations without a body
    is_extern: bool = False
    var_arg: bool = False
    line: int = _line()
    file: str = _file()


@dataclass
class Field:
    """
    A struct field with its name and type annotation.
    """
    name: str
    type: str


@dataclass
class Struct:
    """
    A struct declaration with its name and ordered fields.
    """
    name: str
    fields: list[Field] | None  # None for forward declarations without a body
    line: int = _line()
    file: str = _file()


@dataclass
class Variant:
    """
    One enum member declaration: its name and an optional explicit value.
    """
    name: str
    value: Expr | None = None


@dataclass
class Enum:
    """
    An enum declaration: named integer constants over a backing type,
    accessed through 'Name::member'.
    """
    name: str
    type: str
    members: list[Variant]
    line: int = _line()
    file: str = _file()


@dataclass
class Const:
    """
    An '@const' declaration: a named compile-time constant expression,
    substituted at its uses, with an optional type annotation.
    """
    name: str
    type: str | None
    value: Expr
    line: int = _line()
    file: str = _file()


@dataclass
class Include:
    """
    An '@include' of another source file by its include path (e.g. 'libc/stdio').
    """
    path: str


@dataclass
class Program:
    """
    The root of the AST: the includes, structs, functions, constants, and
    enums of a source file.
    """
    includes: list[Include]
    functions: list[Function]
    structs: list[Struct] = field(default_factory=list)
    consts: list[Const] = field(default_factory=list)
    enums: list[Enum] = field(default_factory=list)
