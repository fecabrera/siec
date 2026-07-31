"""Lifecycle boundary for non-callable declaration inventories."""

from siec.ast import Program
from siec.codegen.generator import CodeGenerator


def complete_declaration_inventory(gen: CodeGenerator,
                                   program: Program) -> None:
    """Verify and freeze every active alias, enum, and extension identity."""
    families = (
        ("alias", program.aliases, gen.collected_aliases),
        ("enum", program.enums, gen.collected_enums),
        ("extension", program.extends, gen.collected_extensions),
    )
    missing = [
        kind
        for kind, declarations, collected in families
        if any(id(declaration) not in collected for declaration in declarations)
    ]
    if missing:
        names = ", ".join(dict.fromkeys(missing))
        raise RuntimeError(
            f"active {names} declarations bypassed collection")

    gen.declaration_inventory_complete = True
