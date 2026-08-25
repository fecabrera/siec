"""Phase-scoped compilation state extracted from CodeGenerator.

These containers hold the registries and thread-locals that used to live as a
flat bag on ``CodeGenerator``. The generator still exposes every field through
attribute forwarding so existing call sites keep working, while phases can
take the narrower context they need.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from llvmlite import ir

if TYPE_CHECKING:
    from siec.ast import Function, Program
    from siec.codegen.generator import EnumInfo, StructInfo


@dataclass
class SourceContext:
    """Files, imports, includes, visibility, and the active source view."""

    module_bindings: dict[tuple[str, str], str] = field(default_factory=dict)
    member_bindings: dict[tuple[str, str], str] = field(default_factory=dict)
    member_targets: dict[tuple[str, str], tuple] = field(default_factory=dict)
    import_targets: dict[tuple[str, str], str] = field(default_factory=dict)
    module_exports: dict[str, set] = field(default_factory=dict)
    local_type_symbols: dict[tuple[str, str], str] = field(default_factory=dict)
    module_type_symbols: dict[tuple[str, str], str] = field(default_factory=dict)
    visible: dict[str, set] = field(default_factory=dict)
    builtin_names: set = field(default_factory=set)
    include_closure: dict[str, set] = field(default_factory=dict)
    entry_files: list = field(default_factory=list)
    unit_files: set | None = None
    current_file: str = ""
    current_line: int = 0
    current_function: str | None = None
    ungated_types: int = 0
    inactive_regions: dict[str, list[tuple[int, int, int, int]]] = field(
        default_factory=dict)
    program: Program | None = None

    @contextmanager
    def in_file(self, path: str | None):
        """Resolve names under ``path`` for the duration of the block."""
        previous = self.current_file
        if path is not None:
            self.current_file = path
        try:
            yield
        finally:
            self.current_file = previous

    @contextmanager
    def ungated(self):
        """Bypass file visibility for compiler-stamped type names."""
        self.ungated_types += 1
        try:
            yield
        finally:
            self.ungated_types -= 1


@dataclass
class SymbolTable:
    """Declarations, overloads, linkage, privacy, and call metadata."""

    return_types: dict[str, str | None] = field(default_factory=dict)
    param_types: dict[str, list[str]] = field(default_factory=dict)
    param_defaults: dict[str, tuple[list, str]] = field(default_factory=dict)
    resolved_functions: dict[str, Function] = field(default_factory=dict)
    function_signatures: dict[str, tuple] = field(default_factory=dict)
    raw_callables: dict[str, list] = field(default_factory=dict)
    callable_declarations: list = field(default_factory=list)
    collected_callables: set[int] = field(default_factory=set)
    callable_inventory_complete: bool = False
    callables_resolved: bool = False
    noreturns: set[str] = field(default_factory=set)
    self_returns: set[str] = field(default_factory=set)
    overloads: dict[str, list[tuple[tuple, str]]] = field(default_factory=dict)
    deferred_overloads: dict[str, object] = field(default_factory=dict)
    generic_overloads: dict[str, list] = field(default_factory=dict)
    variadics: set[str] = field(default_factory=set)
    var_args: set[str] = field(default_factory=set)
    private_methods: dict[str, set[str]] = field(default_factory=dict)
    overridden_functions: set[int] = field(default_factory=set)
    overridden_method_signatures: dict[str, set[tuple]] = field(
        default_factory=dict)
    deprecated: dict[str, str] = field(default_factory=dict)
    removed: dict[str, str] = field(default_factory=dict)
    call_graph: dict = field(default_factory=dict)
    conditional_call_graph: dict[tuple[str, str], set[str]] = field(
        default_factory=dict)
    any_types: dict[str | None, set[str]] = field(default_factory=dict)
    live_any_types: set[str] | None = None
    runtime_type_guard: str | None = None
    call_sites: dict[tuple[str, str], tuple[str, int]] = field(
        default_factory=dict)
    type_instantiation_sites: dict[str, tuple[str, str, int]] = field(
        default_factory=dict)
    instantiation_sites: dict[str, tuple[str, str, int]] = field(
        default_factory=dict)
    deprecated_uses: list = field(default_factory=list)
    statics: dict[tuple[str, str], str] = field(default_factory=dict)
    symbol_names: dict[str, str] = field(default_factory=dict)
    symbol_files: dict[str, str] = field(default_factory=dict)
    abi_args: dict[str, list] = field(default_factory=dict)
    abi_returns: dict[str, tuple] = field(default_factory=dict)
    globals: dict[str, str] = field(default_factory=dict)
    resolved_globals: dict[str, object] = field(default_factory=dict)
    constants: dict = field(default_factory=dict)
    resolved_constants: set[int] = field(default_factory=set)
    constants_resolved: bool = False
    macros: dict = field(default_factory=dict)


@dataclass
class TypeRegistry:
    """Aliases, structs, enums, interfaces, extensions, and layout claims."""

    structs: dict[str, StructInfo] = field(default_factory=dict)
    enums: dict[str, EnumInfo] = field(default_factory=dict)
    enum_declarations: list = field(default_factory=list)
    collected_enums: set[int] = field(default_factory=set)
    enum_member_declarations: dict[tuple[str, str], tuple] = field(
        default_factory=dict)
    resolved_enums: set[int] = field(default_factory=set)
    aliases: dict[str, str] = field(default_factory=dict)
    alias_declarations: list = field(default_factory=list)
    collected_aliases: set[int] = field(default_factory=set)
    alias_targets: dict[str, str] = field(default_factory=dict)
    resolved_aliases: set[int] = field(default_factory=set)
    generic_aliases: dict = field(default_factory=dict)
    interfaces: dict = field(default_factory=dict)
    interface_actions: dict = field(default_factory=dict)
    implements: dict[str, set] = field(default_factory=dict)
    interface_queries: set[tuple[str, str]] = field(default_factory=set)
    pending_conformance: deque[tuple] = field(default_factory=deque)
    resolved_conformance: deque[tuple] = field(default_factory=deque)
    extension_declarations: list = field(default_factory=list)
    collected_extensions: set[int] = field(default_factory=set)
    resolved_extensions: set[int] = field(default_factory=set)
    checked_extensions: set[int] = field(default_factory=set)
    array_claims: list[tuple[str, str, dict | None, str]] = field(
        default_factory=list)
    generic_claims: list[tuple[str, list[str], dict | None, str]] = field(
        default_factory=list)
    generic_struct_claims: dict[str, list[tuple]] = field(default_factory=dict)
    declaration_inventory_complete: bool = False


@dataclass
class GenericRegistry:
    """Templates, instances, recursion tracking, and semantic work queues."""

    generic_structs: dict = field(default_factory=dict)
    generic_functions: dict = field(default_factory=dict)
    generic_methods: dict = field(default_factory=dict)
    generic_receiver_methods: dict[str, list] = field(default_factory=dict)
    instantiated_functions: set = field(default_factory=set)
    pending_functions: deque = field(default_factory=deque)
    function_instance_states: dict[str, str] = field(default_factory=dict)
    checked_functions: set[str] = field(default_factory=set)
    checked_instance_bodies: list = field(default_factory=list)
    checked_default_types: set[str] = field(default_factory=set)
    generic_type_depth: int = 0
    checking_function: Function | None = None
    checking_loop_depth: int = 0
    checked_call: str | None = None
    semantic_complete: bool = False


@dataclass
class FlowContext:
    """Scopes' companion emission state: ownership, defers, and control flow."""

    emit_targets: list[tuple] = field(default_factory=list)
    defer_frames: list[list] = field(default_factory=list)
    borrowed_temporary_frames: list[list] = field(default_factory=list)
    flushing_defers: int = 0
    loop_targets: list[tuple] = field(default_factory=list)
    flush_loop_floors: list[int] = field(default_factory=list)
    temporary_count: int = 0

    @contextmanager
    def nested_function(self):
        """Isolate per-function stacks while emitting a nested LLVM body.

        Closures are lowered while an outer call's temporary frame is still
        open. Without a fresh stack, inner allocas are destroyed in the
        caller's function, which LLVM rejects as an undefined value.
        """
        previous = (
            self.emit_targets,
            self.defer_frames,
            self.borrowed_temporary_frames,
            self.flushing_defers,
            self.loop_targets,
            self.flush_loop_floors,
        )
        self.emit_targets = []
        self.defer_frames = []
        self.borrowed_temporary_frames = []
        self.flushing_defers = 0
        self.loop_targets = []
        self.flush_loop_floors = []
        try:
            yield
        finally:
            (self.emit_targets,
             self.defer_frames,
             self.borrowed_temporary_frames,
             self.flushing_defers,
             self.loop_targets,
             self.flush_loop_floors) = previous


@dataclass
class EmissionContext:
    """LLVM module, target information, builders' shared pools, and debug."""

    target: str = ""
    module: ir.Module | None = None
    debug: object | None = None
    string_pool: dict[str, ir.GlobalVariable] = field(default_factory=dict)
    str_count: int = 0
    any_names: dict[int, str] = field(default_factory=dict)
    typename_fn: object | None = None
    emitting: bool = False
    types_lowered: bool = False
    functions_lowered: bool = False
    # Structured warnings/notes accumulated during this compilation.
    diagnostics: list = field(default_factory=list)

    @classmethod
    def create(cls, module_name: str, target: str | None = None) -> EmissionContext:
        from llvmlite import binding

        triple = target or binding.get_default_triple()
        module = ir.Module(name=module_name, context=ir.Context())
        module.triple = triple
        return cls(target=triple, module=module)


@dataclass(frozen=True)
class SemanticModel:
    """
    Immutable typed-program snapshot available after semantic checking.

    Tooling and emission should prefer reading this over mutating generator
    registries. Mappings are read-only views of the checked inventories.
    """

    structs: Mapping[str, StructInfo]
    enums: Mapping[str, EnumInfo]
    aliases: Mapping[str, str]
    interfaces: Mapping
    implements: Mapping[str, frozenset]
    return_types: Mapping[str, str | None]
    param_types: Mapping[str, tuple[str, ...]]
    overloads: Mapping[str, tuple]
    macros: Mapping
    constants: Mapping
    globals: Mapping[str, str]
    module_exports: Mapping[str, frozenset]
    visible: Mapping[str, frozenset]
    inactive_regions: Mapping[str, tuple]

    @classmethod
    def snapshot(cls, gen) -> SemanticModel:
        """Freeze the checked inventories from a completed generator."""
        return cls(
            structs=MappingProxyType(dict(gen.structs)),
            enums=MappingProxyType(dict(gen.enums)),
            aliases=MappingProxyType(dict(gen.aliases)),
            interfaces=MappingProxyType(dict(gen.interfaces)),
            implements=MappingProxyType({
                name: frozenset(claims)
                for name, claims in gen.implements.items()
            }),
            return_types=MappingProxyType(dict(gen.return_types)),
            param_types=MappingProxyType({
                name: tuple(params)
                for name, params in gen.param_types.items()
            }),
            overloads=MappingProxyType({
                name: tuple(candidates)
                for name, candidates in gen.overloads.items()
            }),
            macros=MappingProxyType(dict(gen.macros)),
            constants=MappingProxyType(dict(gen.constants)),
            globals=MappingProxyType(dict(gen.globals)),
            module_exports=MappingProxyType({
                file: frozenset(names)
                for file, names in gen.module_exports.items()
            }),
            visible=MappingProxyType({
                file: frozenset(names)
                for file, names in gen.visible.items()
            }),
            inactive_regions=MappingProxyType({
                file: tuple(regions)
                for file, regions in gen.inactive_regions.items()
            }),
        )


# Field name -> owning context attribute on CodeGenerator.
CONTEXT_FIELDS: dict[str, str] = {}

for _owner, _cls in (
    ("source", SourceContext),
    ("symbols", SymbolTable),
    ("types", TypeRegistry),
    ("generics", GenericRegistry),
    ("flow", FlowContext),
    ("emission", EmissionContext),
):
    for _name in _cls.__dataclass_fields__:
        CONTEXT_FIELDS[_name] = _owner
