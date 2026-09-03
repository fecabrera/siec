"""Resolution of overloaded functions: one name, distinct parameter lists.

A name's overloads live under mangled sibling symbols, and a call picks
among them by its argument types. Conversion severity ranks first, then the
number of conversions; no fit, or an equal conversion profile, is an error.
An argument ranks at its declared Sie type, a literal at its smallest default
signed type: 'i32', 'i64', or 'i128'.
"""

from typing import NamedTuple

from siec.ast import (
    AggregateLiteral,
    ArrayLiteral,
    FloatLiteral,
    Function,
    IntLiteral,
    NullLiteral,
    SizeOf,
    StrLiteral,
    TypeId,
    Var,
)
from siec.codegen.generator import CodeGenerator
from siec.codegen.inference import (
    enum_backing,
    expr_sie_type,
    infer_type,
    integer_literal_type,
    numeric_class,
    type_info,
)
from siec.codegen.types import (
    is_aliasing,
    is_const,
    is_reference,
    strip_const,
    strip_reference,
)


class FitProfile(NamedTuple):
    """The number and severity of conversions a candidate requires."""

    adopted: int = 0
    implicit: int = 0

    @property
    def tier(self) -> str:
        if self.adopted:
            return "adopt"
        if self.implicit:
            return "implicit"
        return "exact"

    @property
    def rank(self) -> tuple[int, int, int]:
        """Severity first, then fewer conversions within that severity."""
        strength = {"exact": 0, "implicit": 1, "adopt": 2}
        return strength[self.tier], self.adopted, self.implicit


def display_name(symbol: str) -> str:
    """
    The Sie name behind a module symbol, for error messages: the
    signature and any static mangling stripped.
    """
    return symbol.partition("(")[0].split(".static.")[0]


def shown_signature(fn: Function) -> str:
    """
    A function's name with its parameter types, for error messages
    naming one signature of an overloaded name: 'f(i64, char*)', a
    variadic's pack shown as its '...'. An interface-typed parameter's
    synthetic type parameter renders back as the interface it spells.
    """
    from siec.codegen.generics import substitute

    mapping = {param: constraint
               for param, constraint in (fn.constraints or {}).items()
               if param.startswith("__")}

    params = [substitute(p.type, mapping) if mapping else p.type
              for p in fn.params]
    if fn.variadic:
        params[-1] = "..."

    return f"{fn.name}({', '.join(params)})"


def overload_key(params) -> tuple:
    """
    The signature identity of a parameter list: its types behind 'const',
    which marks a contract rather than a distinct type.
    """
    return tuple(strip_const(p.type) for p in params)


def overload_symbol(gen: CodeGenerator, symbol: str, params) -> str:
    """
    The module symbol a function's own signature lives under: its sibling
    in the name's overload set, or the symbol itself when never entered.
    """
    for key, sibling in gen.overloads.get(symbol, ()):
        if key == overload_key(params):
            return sibling

    return symbol


def overload_candidates(gen: CodeGenerator, symbol: str) -> list[str]:
    """
    The module symbols a Sie name's functions live under: its overload
    set's, in declaration order, or the symbol itself for a fixed name
    ('@extern', '@symbol', 'main', a generic instance).
    """
    entry = gen.overloads.get(symbol)
    return [sibling for _, sibling in entry] if entry else [symbol]


def declare_overload(gen: CodeGenerator, fn: Function, symbol: str) -> str:
    """
    Enter a declaration into its name's overload set: a matching signature
    is a redeclaration reusing its symbol, and a new one takes a symbol
    mangled from its parameter types - 'f(i64)' - so separately compiled
    units name each signature alike, whatever their declaration order.
    '@extern' functions and 'main' name one fixed symbol, and an
    '@symbol' function picks its own, so none of them overload or mangle.
    """
    key = overload_key(fn.params)

    if fn.is_extern or fn.symbol is not None or fn.name == "main":
        entry = gen.overloads.get(symbol)
        if entry is not None and all(known != key for known, _ in entry):
            what = "'@extern'" if fn.is_extern else (
                "'main'" if fn.name == "main" else "'@symbol'")
            raise TypeError(f"cannot overload {what} function {fn.name!r}")

        return symbol

    entry = gen.overloads.setdefault(symbol, [])
    for known, sibling in entry:
        if known == key:
            return sibling

    sibling = f"{symbol}({','.join(key)})"
    entry.append((key, sibling))
    return sibling


def overload_entries(gen: CodeGenerator, symbol: str,
                     module: str | None = None) -> list | None:
    """
    Overload candidates for a name, optionally restricted to one module.

    A qualified or member-imported call names a specific module's 'free';
    another module's mangled 'free(opaque*)' must not steal it.
    """
    entry = gen.overloads.get(symbol)
    if entry is None:
        return None
    if module is None:
        return entry

    closure = gen.include_closure.get(module, {module})
    return [
        (key, sibling) for key, sibling in entry
        if (fn := gen.resolved_functions.get(sibling)) is not None
        and fn.file in closure
    ]


def pick_overload(gen: CodeGenerator, symbol: str, args: list, scope: dict,
                  receiver: str | None = None,
                  module: str | None = None) -> str:
    """
    Pick the overload a call's arguments select. Conversion severity ranks
    first, then the number of converted arguments. No viable candidate, or
    an equal conversion profile, is an error.

    A constructor passes its instance's type as 'receiver', standing in
    for the receiver argument it has yet to build. 'module' limits the
    set to that module's declarations, so 'stdlib.free' stays the
    '@extern' when 'std.alloc' also defines a 'free'.
    """
    return pick_overload_fit(
        gen, symbol, args, scope, receiver=receiver, module=module)[0]


def pick_overload_fit(gen: CodeGenerator, symbol: str, args: list, scope: dict,
                      receiver: str | None = None,
                      module: str | None = None,
                      method_receiver=None) -> tuple[str, FitProfile]:
    """
    Pick a concrete overload and also return its conversion-strength tier.

    Call arbitration uses the tier to compare this candidate with a generic
    one.  Keeping that comparison outside the concrete overload set prevents
    an implicit concrete conversion from bypassing an exact generic match.
    """
    entry = overload_entries(gen, symbol, module)
    if entry is None:
        return symbol, FitProfile()
    if not entry:
        # This module has no mangled sibling; keep the base symbol
        # ('@extern free' beside another module's 'free(opaque*)').
        return symbol, FitProfile()

    arg_types = [rank_type(gen, arg, scope) for arg in args]
    ranked_args = [adapting_value(gen, arg, scope) for arg in args]
    method_type = (rank_type(gen, method_receiver, scope)
                   if method_receiver is not None else None)
    ranked_receiver = (adapting_value(gen, method_receiver, scope)
                       if method_receiver is not None else None)

    def candidate_arguments(candidate: str) -> tuple[list, list]:
        """The written arguments, plus this candidate's hidden receiver."""
        candidate_args = ranked_args
        candidate_types = arg_types
        if method_receiver is not None:
            from siec.codegen.methods import takes_receiver

            if takes_receiver(gen, candidate):
                candidate_args = [ranked_receiver, *candidate_args]
                candidate_types = [method_type, *candidate_types]
        if receiver is not None:
            candidate_args = [None, *candidate_args]
            candidate_types = [receiver, *candidate_types]
        return candidate_args, candidate_types

    # a lone candidate resolves as ever, unless a generic template shares
    # the name and the arguments must decide between the two
    if len(entry) == 1 and gen.generic_functions.get(symbol) is None:
        candidate = entry[0][1]
        candidate_args, candidate_types = candidate_arguments(candidate)
        fit = candidate_fit(gen, candidate, candidate_args, candidate_types)
        # Arity/type diagnostics are emitted later for a lone declaration,
        # matching the historical behavior of returning it unconditionally.
        return candidate, fit or FitProfile()

    # Surface the precise reason an argument has no type before ranking:
    # otherwise an undefined name or call matches every candidate as an
    # adaptable expression and reports an unrelated overload ambiguity.
    for arg, arg_type in zip(args, arg_types):
        if arg_type is None:
            from siec.codegen.inference import untyped_reason

            if (reason := untyped_reason(gen, arg, scope)) is not None:
                raise reason

        if arg_type is None and isinstance(arg, Var):
            from siec.codegen.checking import check_expression

            check_expression(gen, arg, scope)

    ranked = []
    for _, candidate in entry:
        candidate_args, candidate_types = candidate_arguments(candidate)
        fit = candidate_fit(gen, candidate, candidate_args, candidate_types)
        if fit is not None:
            ranked.append((fit.rank, candidate, fit))

    name = display_name(symbol)

    if not ranked:
        shown = ", ".join(arg_type or "?" for arg_type in arg_types)
        raise TypeError(f"no overload of {name!r} takes ({shown})")

    best = min(rank for rank, _, _ in ranked)
    pool = [(candidate, fit) for rank, candidate, fit in ranked
            if rank == best]
    if len(pool) > 1:
        signatures = "; ".join(
            f"({', '.join(gen.param_types.get(candidate, ()))})"
            for candidate, _ in pool)
        raise TypeError(f"call to {name!r} is ambiguous between {signatures}")

    return pool[0]


def candidate_fit(gen: CodeGenerator, symbol: str, args: list,
                  arg_types: list) -> FitProfile | None:
    """
    Count how a candidate's parameters take a call's arguments: 'exact',
    'implicit' conversion, or a literal's 'adopt'. Return None when an
    argument does not fit or the argument count is invalid.
    """
    params = gen.param_types.get(symbol, [])

    # an 'args...' candidate takes any extras; only its fixed
    # parameters rank the fit, the pack coming after the pick
    arity = gen.call_arities[symbol]
    if not arity.accepts(len(args)):
        return None

    if arity.variadic:
        fixed = len(params) - 1
        args, arg_types, params = args[:fixed], arg_types[:fixed], params[:fixed]

    adopted = 0
    implicit = 0
    for arg, arg_type, param in zip(args, arg_types, params):
        one = parameter_fit(gen, arg, arg_type, param)
        if one is None:
            return None

        if one == "adopt":
            adopted += 1
        elif one == "implicit":
            implicit += 1

    return FitProfile(adopted, implicit)


def parameter_fit(gen: CodeGenerator, arg, arg_type: str | None,
                  param: str) -> str | None:
    """
    How one argument fits a parameter: 'exact' on the very type,
    'implicit' through a conversion calls already apply - same-prefix
    widening, array decay, 'opaque*' adoption, a 'null' literal.

    Declared parameter types are already canonical - declaration expanded
    their aliases - so no view-gated expansion happens here.
    """
    from siec.codegen.types import is_nonnull_pointer, strip_nonnull

    target = strip_const(param)

    # A mutable reference aliases its argument in place and therefore needs
    # the exact type. A const reference may instead borrow a materialized
    # converted value, so ordinary implicit conversion participates in
    # overload selection for it.
    if is_reference(target):
        if arg_type is None:
            return (parameter_fit(gen, arg, arg_type,
                                  strip_const(strip_reference(param)))
                    if is_const(param) else None)

        if strip_const(arg_type) == strip_const(strip_reference(target)):
            return "exact"
        if is_const(param):
            return parameter_fit(
                gen, arg, arg_type, strip_const(strip_reference(param)))
        return None

    # an untypeable argument adapts to what its shape can fill: an
    # aggregate literal a struct or array parameter with as many fields,
    # an array literal an array or a pointer. A bare name with no type is
    # not a wildcard - matching every candidate as 'implicit' turns an
    # undefined variable into an ambiguity among overloads.
    if arg_type is None:
        if isinstance(arg, AggregateLiteral):
            info = type_info(gen, target)
            if info is None or info.fields is None:
                return None

            if arg.names is None and len(arg.elements) != len(info.fields):
                return None

            return "implicit"

        if isinstance(arg, ArrayLiteral):
            return ("implicit" if target.endswith("[]") or target.endswith("*")
                    else None)

        if isinstance(arg, Var):
            return None

        return "implicit"

    source = strip_const(arg_type)

    # an aliasing const value never fits a mutable parameter
    if is_const(arg_type) and is_aliasing(source) and not is_const(param):
        return None

    if source == target:
        return "exact"

    nullable_source = strip_const(strip_nonnull(source))
    nullable_target = strip_const(strip_nonnull(target))
    if nullable_source == nullable_target and nullable_source.endswith("*"):
        if is_nonnull_pointer(source) or is_nonnull_pointer(target):
            return "implicit"

    # 'null' adopts any pointer parameter, and a string literal fills a
    # 'char[]' one as the fat value it already is, length included
    if (isinstance(arg, NullLiteral) and target.endswith("*")
            and not is_nonnull_pointer(target)):
        return "implicit"

    if isinstance(arg, StrLiteral) and target == "char[]":
        return "implicit"

    # any pointer or array decays to 'opaque*', an array to its element pointer
    if target == "opaque*" and (source.endswith("*") or source.endswith("[]")
                                or source.startswith("fn(")):
        return "implicit"

    if (source.endswith("[]")
            and strip_nonnull(target) == f"{source[:-2]}*"):
        return "implicit"

    # numbers widen within their prefix, enums through their backing type
    from_class = numeric_class(enum_backing(gen, source))
    to_class = numeric_class(enum_backing(gen, target))
    if (from_class is not None and to_class is not None
            and from_class[0] == to_class[0] and from_class[1] <= to_class[1]):
        return "implicit"

    # an untyped literal adopts any numeric parameter it emits into, the
    # loosest fit: any candidate its default type reaches wins over it
    if to_class is not None and (
            isinstance(arg, (IntLiteral, SizeOf, TypeId))
            or (isinstance(arg, FloatLiteral) and to_class[0] == "f")):
        return "adopt"

    return None


def adapting_value(gen: CodeGenerator, arg, scope: dict):
    """
    The value an unannotated '@const' stands for at this use, or the
    argument itself. Matching and ranking then see a literal written in
    place, including adopting into a wider numeric parameter.
    """
    if not isinstance(arg, Var) or arg.name in scope:
        return arg

    if not getattr(arg, "qualified", False) and not gen.sees(arg.name):
        return arg

    from siec.codegen.constants import find_constant

    const = find_constant(gen, arg.name, getattr(arg, "module_file", None))
    if const is None or const.type is not None:
        return arg

    return const.value


def rank_type(gen: CodeGenerator, arg, scope: dict) -> str | None:
    """
    The type an argument ranks at: its declared Sie type, or a literal's
    default - an integer literal ranks as i32, i64, or i128 according to
    the first width it fits. An unannotated '@const' ranks like its value
    written in place, so 'pick(N)' with '@const N = 5' matches as 'pick(5)'.
    """
    arg = adapting_value(gen, arg, scope)

    declared = expr_sie_type(gen, arg, scope)
    if declared is not None:
        return declared

    if isinstance(arg, IntLiteral):
        return integer_literal_type(arg.value)

    return infer_type(gen, arg, scope)
