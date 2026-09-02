"""One structural representation for canonical Sie type spellings."""

from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class TypeRef:
    """A resolved type shape independent of semantic and LLVM registries."""

    kind: str
    name: str | None = None
    inner: "TypeRef | None" = None
    items: tuple["TypeRef", ...] = ()
    fields: tuple[tuple[str, "TypeRef"], ...] = ()
    result: "TypeRef | None" = None
    size: str | None = None

    def spelling(self) -> str:
        """Return the canonical string form used by existing compiler APIs."""
        if self.kind == "name":
            return self.name or ""
        if self.kind == "const":
            return f"const {self.inner.spelling()}"
        if self.kind == "reference":
            return f"&{self.inner.spelling()}"
        if self.kind == "nonnull":
            return f"!{self.inner.spelling()}"
        if self.kind == "pointer":
            return f"{self.inner.spelling()}*"
        if self.kind == "array":
            return f"{self.inner.spelling()}[]"
        if self.kind == "sized":
            return f"{self.inner.spelling()}[{self.size}]"
        if self.kind == "raw":
            return f"raw<{self.inner.spelling()}>[{self.size}]"
        if self.kind == "generic":
            args = ",".join(item.spelling() for item in self.items)
            return f"{self.name}<{args}>"
        if self.kind in ("struct", "union"):
            body = ";".join(
                f"{name}:{type_.spelling()}" for name, type_ in self.fields)
            return f"{self.kind}{{{body}}}"
        if self.kind in ("function", "closure"):
            prefix = "closure " if self.kind == "closure" else ""
            args = ",".join(item.spelling() for item in self.items)
            value = f"{prefix}fn({args})"
            if self.result is not None:
                value += f"->{self.result.spelling()}"
            return value
        raise ValueError(f"unknown type reference kind {self.kind!r}")

def _matching(text: str, start: int, opening: str, closing: str) -> int:
    """Find the delimiter that closes ``text[start]``."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            if closing == ">" and text[index - 1:index] == "-":
                continue
            depth -= 1
            if depth == 0:
                return index
    raise TypeError(f"malformed type {text!r}")


def _split(text: str, separator: str) -> tuple[str, ...]:
    """Split text at one top-level separator."""
    if not text:
        return ()
    pieces = []
    start = 0
    stack = []
    pairs = {">": "<", ")": "(", "]": "[", "}": "{"}
    for index, char in enumerate(text):
        if char in "<([{":
            stack.append(char)
        elif char in ">)]}":
            if char == ">" and text[index - 1:index] == "-":
                continue
            if not stack or stack.pop() != pairs[char]:
                raise TypeError(f"malformed type list {text!r}")
        elif char == separator and not stack:
            pieces.append(text[start:index])
            start = index + 1
    if stack:
        raise TypeError(f"malformed type list {text!r}")
    pieces.append(text[start:])
    return tuple(pieces)


def _suffix(base: TypeRef, text: str, original: str) -> TypeRef:
    """Apply pointer and array derivations in source order."""
    index = 0
    result = base
    while index < len(text):
        if text[index] == "*":
            result = TypeRef("pointer", inner=result)
            index += 1
            continue
        if text.startswith("[]", index):
            result = TypeRef("array", inner=result)
            index += 2
            continue
        if text[index] == "[":
            close = _matching(text, index, "[", "]")
            result = TypeRef(
                "sized", inner=result, size=text[index + 1:close])
            index = close + 1
            continue
        raise TypeError(f"malformed type {original!r}")
    return result


def _core_end(text: str) -> int:
    """Return the first top-level derivation position in a named type."""
    depth = 0
    for index, char in enumerate(text):
        if char == "<":
            depth += 1
        elif char == ">" and text[index - 1:index] != "-":
            depth -= 1
        elif depth == 0 and char in "*[":
            return index
    return len(text)


@lru_cache(maxsize=None)
def parse_type_ref(spelling: str) -> TypeRef:
    """Parse one canonical type spelling, cached for all compiler phases."""
    if not spelling:
        raise TypeError("empty type spelling")

    if spelling.startswith("const "):
        return TypeRef("const", inner=parse_type_ref(spelling[6:]))
    if spelling.startswith("&"):
        return TypeRef("reference", inner=parse_type_ref(spelling[1:]))
    if spelling.startswith("!"):
        return TypeRef("nonnull", inner=parse_type_ref(spelling[1:]))

    closure = spelling.startswith("closure fn(")
    if closure or spelling.startswith("fn("):
        opening = spelling.index("(")
        close = _matching(spelling, opening, "(", ")")
        params = tuple(
            parse_type_ref(value)
            for value in _split(spelling[opening + 1:close], ","))
        rest = spelling[close + 1:]
        result = None
        if rest.startswith("->"):
            result = parse_type_ref(rest[2:])
            rest = ""
        base = TypeRef(
            "closure" if closure else "function",
            items=params,
            result=result,
        )
        return _suffix(base, rest, spelling)

    if spelling.startswith("struct{") or spelling.startswith("union{"):
        opening = spelling.index("{")
        close = _matching(spelling, opening, "{", "}")
        fields = []
        for field in _split(spelling[opening + 1:close], ";"):
            if ":" not in field:
                raise TypeError(f"malformed anonymous type {spelling!r}")
            name, type_name = field.split(":", 1)
            fields.append((name, parse_type_ref(type_name)))
        base = TypeRef(
            "union" if spelling.startswith("union{") else "struct",
            fields=tuple(fields),
        )
        return _suffix(base, spelling[close + 1:], spelling)

    if spelling.startswith("raw<"):
        close = _matching(spelling, 3, "<", ">")
        rest = spelling[close + 1:]
        if not rest.startswith("["):
            raise TypeError(f"malformed raw array type {spelling!r}")
        end = _matching(rest, 0, "[", "]")
        base = TypeRef(
            "raw",
            inner=parse_type_ref(spelling[4:close]),
            size=rest[1:end],
        )
        return _suffix(base, rest[end + 1:], spelling)

    end = _core_end(spelling)
    core, rest = spelling[:end], spelling[end:]
    if "<" in core:
        opening = core.index("<")
        close = _matching(core, opening, "<", ">")
        if close != len(core) - 1:
            raise TypeError(f"malformed generic type {spelling!r}")
        base = TypeRef(
            "generic",
            name=core[:opening],
            items=tuple(
                parse_type_ref(value)
                for value in _split(core[opening + 1:close], ",")),
        )
    else:
        base = TypeRef("name", name=core)
    return _suffix(base, rest, spelling)


def derivation(ref: TypeRef) -> tuple[TypeRef, str]:
    """Return a type's base node and its trailing derivation spelling."""
    suffix = ""
    while ref.kind in ("pointer", "array", "sized"):
        if ref.kind == "pointer":
            part = "*"
        elif ref.kind == "array":
            part = "[]"
        else:
            part = f"[{ref.size}]"
        suffix = part + suffix
        ref = ref.inner
    return ref, suffix
