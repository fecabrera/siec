"""Resolution of overloaded functions: one name, distinct parameter lists.

A name's overloads live under mangled sibling symbols, and a call picks
among them by its argument types, ranked exact match over implicit
conversion; no fit, or a tie between conversions, is a compile-time error.
An argument ranks at its declared Sie type, a literal at its default -
an integer literal's 'i32', or 'i64' when it doesn't fit one.
"""

from siec.ast import Function, IntLiteral, NullLiteral
from siec.codegen.generator import CodeGenerator
from siec.codegen.inference import (
    enum_backing,
    expr_sie_type,
    infer_type,
    numeric_class,
)
from siec.codegen.types import (
    is_aliasing,
    is_const,
    is_reference,
    strip_const,
    strip_reference,
)


def overload_key(params) -> tuple:
    """
    The signature identity of a parameter list: its types behind 'const',
    which marks a contract rather than a distinct type.
    """
    return tuple(strip_const(p.type) for p in params)


def overload_symbol(gen: CodeGenerator, symbol: str, params) -> str:
    """
    The module symbol a function's own signature lives under: its sibling
    in the name's overload set, or the symbol itself when never overloaded.
    """
    for key, sibling in gen.overloads.get(symbol, ()):
        if key == overload_key(params):
            return sibling

    return symbol


def declare_overload(gen: CodeGenerator, fn: Function, symbol: str) -> str:
    """
    Enter a declaration into its name's overload set: a matching signature
    is a redeclaration reusing its symbol, and a new one takes a mangled
    sibling. '@extern' functions and 'main' name one fixed symbol, and an
    '@symbol' function picks its own, so none of them overload.
    """
    key = overload_key(fn.params)
    fixed = fn.is_extern or fn.symbol is not None or fn.name == "main"

    entry = gen.overloads.get(symbol)
    if entry is None:
        if not fixed:
            gen.overloads[symbol] = [(key, symbol)]

        return symbol

    for known, sibling in entry:
        if known == key:
            return sibling

    if fixed:
        what = "'@extern'" if fn.is_extern else (
            "'main'" if fn.name == "main" else "'@symbol'")
        raise TypeError(f"cannot overload {what} function {fn.name!r}")

    sibling = f"{symbol}.overload.{len(entry)}"
    entry.append((key, sibling))
    return sibling


def pick_overload(gen: CodeGenerator, symbol: str, args: list, scope: dict) -> str:
    """
    Pick the overload a call's arguments select: a candidate every
    argument matches exactly beats one reached through conversions; no
    viable candidate, or a tie between converted ones, is an error.
    """
    entry = gen.overloads.get(symbol)
    if entry is None or len(entry) == 1:
        return symbol

    arg_types = [rank_type(gen, arg, scope) for arg in args]

    exact, converted = [], []
    for _, candidate in entry:
        fit = candidate_fit(gen, candidate, args, arg_types)
        if fit == "exact":
            exact.append(candidate)
        elif fit is not None:
            converted.append(candidate)

    pool = exact or converted
    name = symbol.split(".static.")[0]

    if not pool:
        shown = ", ".join(t or "?" for t in arg_types)
        raise TypeError(f"no overload of {name!r} takes ({shown})")

    if len(pool) > 1:
        signatures = "; ".join(
            f"({', '.join(gen.param_types.get(c, ()))})" for c in pool)
        raise TypeError(f"call to {name!r} is ambiguous between {signatures}")

    return pool[0]


def candidate_fit(gen: CodeGenerator, symbol: str, args: list,
                  arg_types: list) -> str | None:
    """
    How a candidate's parameters take a call's arguments: 'exact' when
    every one matches its parameter's type, 'implicit' when any needs a
    conversion, None when one doesn't fit or the count is off.
    """
    params = gen.param_types.get(symbol, [])

    # trailing defaults make their parameters optional; varargs take extras
    func = gen.module.globals.get(symbol)
    var_arg = func is not None and func.function_type.var_arg
    defaults = gen.param_defaults.get(symbol, ([], None))[0]

    required = len(params)
    while (required and required <= len(defaults)
           and defaults[required - 1] is not None):
        required -= 1

    if len(args) < required or (len(args) > len(params) and not var_arg):
        return None

    fit = "exact"
    for arg, arg_type, param in zip(args, arg_types, params):
        one = parameter_fit(gen, arg, arg_type, param)
        if one is None:
            return None

        if one != "exact":
            fit = "implicit"

    return fit


def parameter_fit(gen: CodeGenerator, arg, arg_type: str | None,
                  param: str) -> str | None:
    """
    How one argument fits a parameter: 'exact' on the very type,
    'implicit' through a conversion calls already apply - same-prefix
    widening, array decay, 'opaque*' adoption, a 'null' literal.
    """
    from siec.codegen.aliases import expand_alias

    param = expand_alias(gen, param)
    target = strip_const(param)

    # a reference parameter aliases its argument in place: exact type only
    if is_reference(target):
        if arg_type is None:
            return None

        return ("exact" if strip_const(arg_type) == strip_const(strip_reference(target))
                else None)

    # an untypeable argument (an aggregate literal, say) adapts to any
    # parameter, at the conversion tier
    if arg_type is None:
        return "implicit"

    source = strip_const(arg_type)

    # an aliasing const value never fits a mutable parameter
    if is_const(arg_type) and is_aliasing(source) and not is_const(param):
        return None

    if source == target:
        return "exact"

    # 'null' adopts any pointer parameter
    if isinstance(arg, NullLiteral) and target.endswith("*"):
        return "implicit"

    # any pointer or array decays to 'opaque*', an array to its element pointer
    if target == "opaque*" and (source.endswith("*") or source.endswith("[]")):
        return "implicit"

    if source.endswith("[]") and target == f"{source[:-2]}*":
        return "implicit"

    # numbers widen within their prefix, enums through their backing type
    from_class = numeric_class(enum_backing(gen, source))
    to_class = numeric_class(enum_backing(gen, target))
    if (from_class is not None and to_class is not None
            and from_class[0] == to_class[0] and from_class[1] <= to_class[1]):
        return "implicit"

    return None


def rank_type(gen: CodeGenerator, arg, scope: dict) -> str | None:
    """
    The type an argument ranks at: its declared Sie type, or a literal's
    default - an integer literal ranks as the 'i32' it emits as, or as
    'i64' when its value doesn't fit one.
    """
    declared = expr_sie_type(gen, arg, scope)
    if declared is not None:
        return declared

    if isinstance(arg, IntLiteral) and not -2**31 <= arg.value < 2**31:
        return "i64"

    return infer_type(gen, arg, scope)
