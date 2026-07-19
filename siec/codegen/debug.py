"""DWARF debug-info emission: line tables, function scopes, and variables.

Built under '-g' and attached to the generator, this maps every emitted
instruction to its source line and describes each variable's storage and
type, so a debugger can set breakpoints by Sie line, step, and inspect.
"""

from pathlib import Path

from llvmlite import ir

from siec.codegen.types import (
    SIGNED_TYPES,
    UNSIGNED_TYPES,
    is_const,
    is_reference,
    raw_array,
    resolve_type,
    strip_const,
    strip_reference,
)

# DWARF encoding and bit width of each scalar type; bool occupies a byte
# in storage, and char is its own type, unsigned like the C it talks to
SCALAR_ENCODINGS = {
    **{name: ("DW_ATE_signed", int(name[1:])) for name in SIGNED_TYPES},
    **{name: ("DW_ATE_unsigned", int(name[1:])) for name in UNSIGNED_TYPES},
    "f32": ("DW_ATE_float", 32),
    "f64": ("DW_ATE_float", 64),
    "bool": ("DW_ATE_boolean", 8),
    "char": ("DW_ATE_unsigned_char", 8),
}


class DebugInfo:
    """
    The module's debug metadata, built incrementally as functions emit.
    """

    def __init__(self, gen, source_name: str):
        self.gen = gen
        self.module = gen.module
        self.files: dict = {}
        self.di_types: dict = {}
        self.locations: dict = {}
        self.scope = None

        # the flags every debug-info consumer requires; DWARF 4 is the
        # dialect both lldb and gdb read everywhere
        i32 = ir.IntType(32)
        self.module.add_named_metadata(
            "llvm.module.flags", [i32(2), "Debug Info Version", i32(3)])
        self.module.add_named_metadata(
            "llvm.module.flags", [i32(2), "Dwarf Version", i32(4)])

        self.unit = self.module.add_debug_info("DICompileUnit", {
            "language": ir.DIToken("DW_LANG_C99"),
            "file": self.file(source_name),
            "producer": "siec",
            "runtimeVersion": 0,
            "isOptimized": False,
            "emissionKind": ir.DIToken("FullDebug"),
        }, is_distinct=True)
        self.module.add_named_metadata("llvm.dbg.cu", self.unit)

        # the declare intrinsic ties a variable's storage to its metadata
        self.expression = self.module.add_debug_info("DIExpression", {})
        declare_type = ir.FunctionType(ir.VoidType(), [ir.MetaDataType()] * 3)
        self.declare = ir.Function(self.module, declare_type, "llvm.dbg.declare")

    def file(self, path: str):
        """
        The DIFile of a source path, created once per file.
        """
        if path not in self.files:
            resolved = Path(path or "?").resolve()
            self.files[path] = self.module.add_debug_info("DIFile", {
                "filename": resolved.name,
                "directory": str(resolved.parent),
            })

        return self.files[path]

    def enter_function(self, fn, func: ir.Function) -> None:
        """
        Open a function's scope: its subprogram becomes the scope of every
        location and variable until the next function enters.
        """
        di_file = self.file(fn.file)

        # the signature is descriptive: a void return is null, and a '&T'
        # parameter shows as the T it aliases
        types = [self.di_type(fn.return_type)]
        types += [self.di_type(strip_reference(p.type)) for p in fn.params]
        sub_type = self.module.add_debug_info("DISubroutineType", {"types": types})

        subprogram = self.module.add_debug_info("DISubprogram", {
            "name": fn.name,
            "linkageName": func.name,
            "scope": di_file,
            "file": di_file,
            "line": fn.line,
            "type": sub_type,
            "scopeLine": fn.line,
            "unit": self.unit,
            "spFlags": ir.DIToken("DISPFlagDefinition"),
        }, is_distinct=True)

        func.set_metadata("dbg", subprogram)
        self.scope = subprogram
        self.locations = {}

    def location(self, line: int):
        """
        The DILocation of a line in the open function, cached per function.
        """
        if line not in self.locations:
            self.locations[line] = self.module.add_debug_info(
                "DILocation", {"line": line, "column": 1, "scope": self.scope})

        return self.locations[line]

    def declare_variable(self, builder: ir.IRBuilder, slot, name: str,
                         type_name: str | None, line: int,
                         arg: int | None = None) -> None:
        """
        Describe a variable to the debugger: its name, type, and the slot
        holding it; parameters carry their 1-based position.
        """
        operands = {
            "name": name,
            "scope": self.scope,
            "file": self.file(self.gen.current_file),
            "line": line,
            "type": self.di_type(type_name),
        }
        if arg is not None:
            operands["arg"] = arg

        variable = self.module.add_debug_info("DILocalVariable", operands)
        builder.call(self.declare, [slot, variable, self.expression])

    def di_type(self, name: str | None):
        """
        The DWARF type of a Sie type name, cached; None describes nothing,
        which a debugger shows as an untyped value.
        """
        if name is None:
            return None

        if name not in self.di_types:
            # the placeholder breaks recursive struct cycles: a pointer to
            # an in-progress type falls back to an untyped pointer
            self.di_types[name] = None
            self.di_types[name] = self.build_type(name)

        return self.di_types[name]

    def build_type(self, name: str):
        """
        Build the DWARF description of one canonical Sie type name.
        """
        if is_const(name):
            return self.module.add_debug_info("DIDerivedType", {
                "tag": ir.DIToken("DW_TAG_const_type"),
                "baseType": self.di_type(strip_const(name)),
            })

        if is_reference(name):
            return self.module.add_debug_info("DIDerivedType", {
                "tag": ir.DIToken("DW_TAG_reference_type"),
                "baseType": self.di_type(strip_reference(name)),
                "size": 64,
            })

        if name.endswith("*"):
            inner = name[:-1]
            return self.module.add_debug_info("DIDerivedType", {
                "tag": ir.DIToken("DW_TAG_pointer_type"),
                "baseType": None if inner == "opaque" else self.di_type(inner),
                "size": 64,
            })

        # an 'X[]' fat array is its two-field struct: data and length
        if name.endswith("[]"):
            return self.fat_array_type(name)

        if (raw := raw_array(name)) is not None and not raw[2]:
            return self.raw_array_type(name, raw[0], int(raw[1]))

        # a function type is called through its pointer
        if name.startswith("fn("):
            return self.module.add_debug_info("DIDerivedType", {
                "tag": ir.DIToken("DW_TAG_pointer_type"),
                "baseType": None,
                "size": 64,
            })

        if name in SCALAR_ENCODINGS:
            encoding, size = SCALAR_ENCODINGS[name]
            return self.module.add_debug_info("DIBasicType", {
                "name": name,
                "size": size,
                "encoding": ir.DIToken(encoding),
            })

        # an enum value reads as its backing integer
        if (enum := self.gen.enums.get(name)) is not None:
            return self.di_type(enum.backing)

        info = self.gen.structs.get(name)
        if info is not None and info.fields:
            return self.struct_type(name, info)

        return None

    def layout(self):
        """
        The target's data layout and the module context, for sizes and offsets.
        """
        from siec.codegen.sizes import target_data

        return target_data(self.gen.target), self.module.context

    def member(self, name: str, base, size: int, offset: int):
        """
        One field of a composite type, at its bit offset.
        """
        return self.module.add_debug_info("DIDerivedType", {
            "tag": ir.DIToken("DW_TAG_member"),
            "name": name,
            "baseType": base,
            "size": size,
            "offset": offset,
        })

    def fat_array_type(self, name: str):
        """
        An 'X[]' array as the '{data: X*, length: u64}' struct it is.
        """
        element = name[:-2]
        members = [
            self.member("data", self.di_type(element + "*"), 64, 0),
            self.member("length", self.di_type("u64"), 64, 64),
        ]
        return self.module.add_debug_info("DICompositeType", {
            "tag": ir.DIToken("DW_TAG_structure_type"),
            "name": name,
            "size": 128,
            "elements": members,
        })

    def raw_array_type(self, name: str, element: str, count: int):
        """
        A '@raw<T>[N]' as a true DWARF array of N elements.
        """
        data, context = self.layout()
        element_size = resolve_type(element, self.gen.structs).get_abi_size(
            data, context=context) * 8

        subrange = self.module.add_debug_info("DISubrange", {"count": count})
        return self.module.add_debug_info("DICompositeType", {
            "tag": ir.DIToken("DW_TAG_array_type"),
            "baseType": self.di_type(element),
            "size": element_size * count,
            "elements": [subrange],
        })

    def struct_type(self, name: str, info):
        """
        A struct or union with each field at its laid-out offset; a union's
        fields all sit at zero, whatever its storage looks like underneath.
        """
        data, context = self.layout()
        size = info.type.get_abi_size(data, context=context) * 8

        members = []
        for index, field in enumerate(info.fields):
            base = self.di_type(field.type)
            if info.is_union:
                field_type = resolve_type(strip_const(field.type), self.gen.structs)
                field_size = field_type.get_abi_size(data, context=context) * 8
                offset = 0
            else:
                field_size = info.type.elements[index].get_abi_size(
                    data, context=context) * 8
                offset = info.type.get_element_offset(data, index, context=context) * 8

            members.append(self.member(field.name, base, field_size, offset))

        tag = "DW_TAG_union_type" if info.is_union else "DW_TAG_structure_type"
        return self.module.add_debug_info("DICompositeType", {
            "tag": ir.DIToken(tag),
            "name": name,
            "size": size,
            "elements": members,
        })
