; Highlighting for Sie.
;
; The primitive type names are not reserved words: 'opaque' names a field in
; zlib's z_stream and 'i64' takes methods through '@extend'. They parse as
; identifiers, so the queries below pick them out by name where they stand
; for types.

; ---------------------------------------------------------------- comments
(line_comment) @comment @spell
(block_comment) @comment @spell

; ------------------------------------------------------------------ literals
(integer_literal) @number
(float_literal) @number.float
(string_literal) @string
(asm_string) @string.special
(char_literal) @character
(escape_sequence) @string.escape
(boolean_literal) @boolean
(null_literal) @constant.builtin
(self) @variable.builtin

; ---------------------------------------------------------------- keywords
[
  "fn"
  "closure"
  "let"
  "struct"
  "union"
  "enum"
  "interface"
] @keyword

"const" @keyword.modifier

[
  "import"
  "from"
] @keyword.import

[
  "if"
  "else"
  "case"
  "when"
] @keyword.conditional

[
  "while"
  "for"
  "foreach"
] @keyword.repeat

[
  "return"
  "break"
  "continue"
  "emit"
  "defer"
  "drop"
] @keyword.return

[
  "try"
  "except"
] @keyword.exception

"as" @keyword.operator
"move" @keyword.operator

; the directives, every one spelled '@name'
[
  "@include"
  "@if"
  "@else"
  "@const"
  "@macro"
  "@type"
  "@where"
  "@extend"
  "@error"
  "@static_assert"
  "@raw"
  "@asm"
  "@sizeof"
  "@typename"
  "@typeid"
  "@typeof"
] @keyword.directive

(attribute_name) @attribute

; ---------------------------------------------------------------- operators
[
  "+" "-" "*" "/" "%" "**"
  "==" "!=" "<" ">" "<=" ">="
  "&" "|" "^" "~" "<<" ">>"
  "=" "+=" "-=" "*=" "/=" "%=" "**="
  "<<=" ">>=" "&=" "|=" "^="
  "->" "?" "??"
] @operator

[
  "and"
  "or"
  "not"
] @keyword.operator

[ "(" ")" "[" "]" "{" "}" ] @punctuation.bracket
[ "," ";" ":" "." "::" ] @punctuation.delimiter
(type_arguments [ "<" ">" ] @punctuation.bracket)
(type_parameters [ "<" ">" ] @punctuation.bracket)
(variadic_parameter "..." @punctuation.special)

; ------------------------------------------------------------------- types
; every written type reaches the tree through 'qualified_type', a plain
; name being the one-element case of a dotted one
(qualified_type (identifier) @type)
(type_parameter name: (identifier) @type.definition)
(struct_declaration name: (identifier) @type)
(enum_declaration name: (identifier) @type)
(alias_declaration name: (identifier) @type)
(anonymous_type [ "struct" "union" ] @keyword)
(type_pattern (identifier) @type)
(array_receiver (identifier) @type)

; the builtin type names, wherever they stand
((identifier) @type.builtin
 (#any-of? @type.builtin
  "i8" "i16" "i32" "i64" "i128" "u8" "u16" "u32" "u64" "u128"
  "f32" "f64" "bool" "char" "opaque"))

; the prelude's own declarations
((identifier) @type.builtin
 (#any-of? @type.builtin
  "Any" "Result" "Tuple" "Slot" "Scalar" "Integer" "SignedInteger" "UnsignedInteger"
  "Iterator" "ConstIterator" "Iterable"
  "ArrayIterator" "ConstArrayIterator" "Enumerated" "EnumerateIterator"
  "Add" "Sub" "Mul" "Div" "Rem"
  "AddAssign" "SubAssign" "MulAssign" "DivAssign" "RemAssign"
  "Eq" "Ord" "GetItem" "SetItem"))

; and the constants it defines for every target
((identifier) @constant.builtin
 (#any-of? @constant.builtin
  "TARGET_OS" "TARGET_ARCH" "TARGET_ENV"
  "OS_DARWIN" "OS_LINUX" "OS_WINDOWS" "OS_NONE" "OS_UNKNOWN"
  "ARCH_X86_64" "ARCH_AARCH64" "ARCH_RISCV64" "ARCH_UNKNOWN"
  "ENV_GNU" "ENV_MUSL" "ENV_MSVC" "ENV_ANDROID" "ENV_ELF" "ENV_UNKNOWN"))

; --------------------------------------------------------------- functions
(function_name name: (identifier) @function)

; in a method the first name is the receiver, the type it acts on; this
; pattern comes second so it wins where both are there
(function_name
  name: (identifier) @type
  method: (identifier) @function.method)
(action_declaration name: (identifier) @function.method)
(method_declaration name: (identifier) @function.method)
(struct_body
  (template_declaration
    (function_declaration
      name: (function_name name: (identifier) @function.method))))

(call_expression function: (identifier) @function.call)
(call_expression function: (field_expression field: (identifier) @function.method.call))
(call_expression function: (arrow_expression field: (identifier) @function.method.call))
(call_expression function: (scoped_identifier name: (identifier) @function.method.call))
(generic_reference name: (identifier) @function.call)

((call_expression function: (identifier) @function.builtin)
 (#any-of? @function.builtin "Ok" "Error" "enumerate"))

; ----------------------------------------------------------------- members
(parameter name: (identifier) @variable.parameter)
(variadic_parameter name: (identifier) @variable.parameter)
(macro_parameters (identifier) @variable.parameter)
(self_parameter "self" @variable.builtin)

(field_declaration name: (identifier) @variable.member)
(field_expression field: (identifier) @variable.member)
(arrow_expression field: (identifier) @variable.member)
(aggregate_field name: (identifier) @variable.member)

(enum_member name: (identifier) @constant)
(scoped_identifier name: (identifier) @constant)
(const_declaration name: (identifier) @constant)
(macro_declaration name: (identifier) @constant.macro)
(global_declaration name: (identifier) @variable)

(import_member name: (identifier) @variable)
(import_member alias: (identifier) @variable)
(module_path (identifier) @module)
