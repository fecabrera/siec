/**
 * Tree-sitter grammar for Sie.
 *
 * The precedence ladder mirrors the compiler's own (siec/parser/expressions.py):
 * ternary over 'or', 'and', comparisons, '|', '^', '&', shifts, additive,
 * multiplicative, '**', then the unary and postfix forms.
 */

const PREC = {
  ternary: 1,
  or: 2,
  and: 3,
  comparison: 4,
  bitor: 5,
  bitxor: 6,
  bitand: 7,
  shift: 8,
  additive: 9,
  multiplicative: 10,
  power: 11,
  cast: 12,
  unary: 13,
  postfix: 14,
};

module.exports = grammar({
  name: "sie",

  extras: ($) => [/\s/, $.line_comment, $.block_comment],

  word: ($) => $.identifier,

  conflicts: ($) => [
    // '{ name = value' opens both an aggregate literal and a block holding
    // an assignment; the ',' or ';' after it decides, so both readings
    // stay alive until one completes
    [$.aggregate_field, $._expression],
    // 'when name[' opens both an index and a 'char[]'-style type pattern
    [$.type_pattern, $._expression],
    // '{ try ...' opens both a block holding a try statement and an
    // aggregate literal holding the same try as a value
    [$.try_statement, $._expression],
    // 'f<i32>' opens both a call and a bare reference to the instance;
    // the '(' after it decides, one token past the '>'
    [$.call_expression, $.generic_reference],
    // 'try f()' completes on its own, so what follows the call decides
    // whether an arm is coming or the whole 'try' is an operand
    [$.try_expression, $._expression],
  ],

  rules: {
    source_file: ($) => repeat($._declaration),

    /* ------------------------------------------------------------------ *
     * declarations
     * ------------------------------------------------------------------ */

    _declaration: ($) =>
      choice(
        $.import_declaration,
        $.include_directive,
        $.conditional_declaration,
        $.const_declaration,
        $.macro_declaration,
        $.alias_declaration,
        $.extend_declaration,
        $.error_directive,
        $.static_assert_directive,
        $.global_declaration,
        $.struct_declaration,
        $.enum_declaration,
        $.function_declaration,
      ),

    import_declaration: ($) =>
      seq(
        "import",
        choice(
          seq(
            "{",
            commaSep(field("member", $.import_member)),
            optional(","),
            "}",
            "from",
            field("path", $.module_path),
          ),
          seq(
            field("path", $.module_path),
            optional(seq("as", field("alias", $.identifier))),
          ),
        ),
        ";",
      ),

    import_member: ($) =>
      seq(
        field("name", $.identifier),
        optional(seq("as", field("alias", $.identifier))),
      ),

    module_path: ($) => sep1($.identifier, "."),

    include_directive: ($) =>
      seq("@include", "(", field("path", $.string_literal), ")", optional(";")),

    error_directive: ($) =>
      seq("@error", "(", field("message", $.string_literal), ")", optional(";")),

    static_assert_directive: ($) =>
      seq(
        "@static_assert",
        "(",
        field("condition", $._expression),
        ",",
        field("message", $.string_literal),
        ")",
        optional(";"),
      ),

    conditional_declaration: ($) =>
      seq(
        "@if",
        "(",
        field("condition", $._expression),
        ")",
        field("consequence", $.declaration_block),
        optional(
          seq(
            "@else",
            field(
              "alternative",
              choice($.declaration_block, $.conditional_declaration),
            ),
          ),
        ),
      ),

    declaration_block: ($) => seq("{", repeat($._declaration), "}"),

    const_declaration: ($) =>
      seq(
        choice("@const", seq(field("visibility", $.attribute), "@const")),
        field("name", $.identifier),
        optional(seq(":", field("type", $.type))),
        "=",
        field("value", $._expression),
        ";",
      ),

    macro_declaration: ($) =>
      seq(
        "@macro",
        field("name", $.identifier),
        optional(field("parameters", $.macro_parameters)),
        choice(
          seq("=", field("value", $._expression), ";"),
          field("body", $.block),
        ),
      ),

    macro_parameters: ($) => seq("(", commaSep($.identifier), ")"),

    alias_declaration: ($) =>
      seq(
        "@type",
        field("name", $.identifier),
        optional(field("type_parameters", $.type_parameters)),
        "=",
        field("type", $.type),
        ";",
      ),

    extend_declaration: ($) =>
      seq(
        "@extend",
        optional(field("type_parameters", $.type_parameters)),
        field("type", $.type),
        ":",
        field("interfaces", commaSep1($.type)),
        choice(";", field("body", $.extend_body)),
      ),

    global_declaration: ($) =>
      seq(
        repeat($.attribute),
        "let",
        field("name", $.identifier),
        ":",
        field("type", $.type),
        optional(seq("=", field("value", $._expression))),
        ";",
      ),

    /* ------------------------------------------------------------------ *
     * types
     * ------------------------------------------------------------------ */

    struct_declaration: ($) =>
      seq(
        repeat($.attribute),
        field("kind", choice("struct", "union", "interface")),
        field("name", $.identifier),
        optional(field("type_parameters", $.type_parameters)),
        optional(seq(":", field("interfaces", commaSep1($.type)))),
        choice(";", seq(field("body", $.struct_body), optional(";"))),
      ),

    struct_body: ($) =>
      seq("{", repeat(choice($.field_declaration, $.action_declaration)), "}"),

    // an interface's body holds the signatures it requires
    action_declaration: ($) =>
      seq(
        "fn",
        field("name", $.identifier),
        optional(field("type_parameters", $.type_parameters)),
        field("parameters", $.parameters),
        optional(seq("->", field("return_type", $.type))),
        ";",
      ),

    // An extension block supplies the receiver for each method. This rule
    // is deliberately receiver-neutral so struct bodies can reuse it when
    // inline struct methods are added.
    extend_body: ($) => seq("{", repeat($.method_declaration), "}"),

    method_declaration: ($) =>
      seq(
        repeat($.attribute),
        "fn",
        field("name", $.identifier),
        optional(field("type_parameters", $.type_parameters)),
        field("parameters", $.parameters),
        optional(seq("->", field("return_type", $.type))),
        choice(field("body", $.block), $.asm_body, ";"),
      ),

    field_declaration: ($) =>
      seq(
        choice(
          seq(
            field("name", $.identifier),
            ":",
            field("type", $.type),
            optional(seq("=", field("default", $._expression))),
          ),
          // an unnamed 'struct { ... }' member hoists its fields
          field("type", $.anonymous_type),
        ),
        ";",
      ),

    anonymous_type: ($) =>
      seq(choice("struct", "union"), field("body", $.struct_body)),

    enum_declaration: ($) =>
      seq(
        repeat($.attribute),
        "enum",
        field("name", $.identifier),
        optional(seq(":", field("type", $.type))),
        "{",
        commaSep($.enum_member),
        optional(","),
        "}",
        optional(";"),
      ),

    enum_member: ($) =>
      seq(
        field("name", $.identifier),
        optional(seq("=", field("value", $._expression))),
      ),

    type_parameters: ($) => seq("<", commaSep1($.type_parameter), ">"),

    /* ------------------------------------------------------------------ *
     * functions
     * ------------------------------------------------------------------ */

    function_declaration: ($) =>
      seq(
        repeat($.attribute),
        "fn",
        field("name", $.function_name),
        field("parameters", $.parameters),
        optional(seq("->", field("return_type", $.type))),
        choice(field("body", $.block), $.asm_body, ";"),
      ),

    // 'name', 'name<T>', 'S::method', 'S<T>::method<U>', 'T[]::method'.
    // One shape parses them all: the '::' is what makes the first name a
    // receiver rather than the function's own
    function_name: ($) =>
      seq(
        field("name", choice($.identifier, $.array_receiver)),
        optional(field("type_parameters", $.type_parameters)),
        optional(
          seq(
            "::",
            field("method", $.identifier),
            optional(field("method_type_parameters", $.type_parameters)),
          ),
        ),
      ),

    array_receiver: ($) => seq($.identifier, "[", "]"),

    parameters: ($) =>
      seq(
        "(",
        commaSep(choice($.self_parameter, $.parameter, $.variadic_parameter, "...")),
        ")",
      ),

    self_parameter: ($) => seq(optional("const"), "&", "self"),

    parameter: ($) =>
      seq(
        field("name", $.identifier),
        ":",
        field("type", $.type),
        optional(seq("=", field("default", $._expression))),
      ),

    // 'args...' is the trailing 'Any[]' sugar
    variadic_parameter: ($) => seq(field("name", $.identifier), "..."),

    attribute: ($) =>
      seq(
        field("name", $.attribute_name),
        optional(seq("(", commaSep($._expression), ")")),
      ),

    attribute_name: ($) => token(seq("@", /[a-zA-Z_]\w*/)),

    asm_body: ($) => $.asm_string,

    /* ------------------------------------------------------------------ *
     * statements
     * ------------------------------------------------------------------ */

    block: ($) => seq("{", repeat($._statement), "}"),

    _statement: ($) =>
      choice(
        $.let_statement,
        $.destructuring_statement,
        $.assignment_statement,
        $.expression_statement,
        $.if_statement,
        $.while_statement,
        $.for_statement,
        $.foreach_statement,
        $.case_statement,
        $.return_statement,
        $.break_statement,
        $.continue_statement,
        $.emit_statement,
        $.defer_statement,
        $.try_statement,
        $.block,
        ";",
      ),

    let_statement: ($) => seq($._let_binding, ";"),

    _let_binding: ($) =>
      seq(
        "let",
        field("name", $.identifier),
        optional(seq(":", field("type", $.type))),
        optional(seq("=", field("value", $._expression))),
      ),

    destructuring_statement: ($) =>
      seq(
        "let",
        field("pattern", $.tuple_pattern),
        "=",
        field("value", $._expression),
        ";",
      ),

    tuple_pattern: ($) =>
      seq("(", commaSep1(choice($.identifier, $.tuple_pattern)), optional(","), ")"),

    assignment_statement: ($) => seq($._assignment_step, ";"),

    expression_statement: ($) => seq($._expression, ";"),

    // a statement whose value is a 'try' with an 'except' arm is closed
    // by the arm's brace, the way an if's body closes an if, so it takes
    // no ';' of its own; a '??' fallback is part of the expression, and
    // an expression_statement covers it
    try_statement: ($) =>
      choice(
        seq(
          "let",
          field("name", $.identifier),
          optional(seq(":", field("type", $.type))),
          "=",
          field("value", $.try_expression),
        ),
        seq(field("target", $._expression), "=", field("value", $.try_expression)),
        seq("emit", field("value", $.try_expression)),
        seq("return", field("value", $.try_expression)),
        field("value", $.try_expression),
      ),

    // the dangling 'else' binds to the nearest 'if'
    if_statement: ($) =>
      prec.right(seq(
        "if",
        "(",
        field("condition", $._expression),
        ")",
        field("consequence", $._statement),
        optional(seq("else", field("alternative", $._statement))),
      )),

    while_statement: ($) =>
      seq(
        "while",
        "(",
        field("condition", $._expression),
        ")",
        field("body", $._statement),
      ),

    // the C-style header: 'init? ; condition? ; step?'
    for_statement: ($) =>
      seq(
        "for",
        "(",
        field("initializer", optional(choice($._let_binding, $._assignment_step, $._expression))),
        ";",
        field("condition", optional($._expression)),
        ";",
        field("step", optional(choice($._assignment_step, $._expression))),
        ")",
        field("body", $._statement),
      ),

    _assignment_step: ($) =>
      seq(
        field("target", $._expression),
        field(
          "operator",
          choice(
            "=", "+=", "-=", "*=", "/=", "%=", "**=",
            "<<=", ">>=", "&=", "|=", "^=",
          ),
        ),
        field("value", $._expression),
      ),

    foreach_statement: ($) =>
      seq(
        "foreach",
        "(",
        field("name", $.identifier),
        ":",
        field("iterable", $._expression),
        ")",
        field("body", $._statement),
      ),

    case_statement: ($) =>
      seq(
        "case",
        "(",
        field("subject", $._expression),
        ")",
        "{",
        repeat($.when_arm),
        optional($.else_arm),
        "}",
      ),

    when_arm: ($) =>
      seq("when", commaSep1(field("value", $._case_value)), ":", repeat($._statement)),

    // an arm may name a type, for '@typeof' subjects. A plain or generic
    // name already parses as an expression and the compiler decides what
    // it meant; only the derived spellings need their own shape
    _case_value: ($) => choice($._expression, $.type_pattern),

    type_pattern: ($) =>
      seq(
        optional("const"),
        $.identifier,
        optional($.type_arguments),
        // '**' lexes as one token, so a doubled pointer names it here
        repeat1(choice("*", "**", seq("[", "]"))),
      ),

    else_arm: ($) => seq("else", ":", repeat($._statement)),

    return_statement: ($) => seq("return", optional($._expression), ";"),
    break_statement: ($) => seq("break", ";"),
    continue_statement: ($) => seq("continue", ";"),
    emit_statement: ($) => seq("emit", $._expression, ";"),

    defer_statement: ($) =>
      seq("defer", choice($.block, seq(choice($._assignment_step, $._expression), ";"))),

    /* ------------------------------------------------------------------ *
     * expressions
     * ------------------------------------------------------------------ */

    _expression: ($) =>
      choice(
        $.identifier,
        $.self,
        $.integer_literal,
        $.float_literal,
        $.string_literal,
        $.char_literal,
        $.boolean_literal,
        $.null_literal,
        $.array_literal,
        $.aggregate_literal,
        $.tuple_expression,
        $.parenthesized_expression,
        $.block_expression,
        $.call_expression,
        $.generic_reference,
        $.scoped_identifier,
        $.field_expression,
        $.arrow_expression,
        $.index_expression,
        $.slice_expression,
        $.unary_expression,
        $.binary_expression,
        $.cast_expression,
        $.ternary_expression,
        $.compile_time_expression,
        $.asm_expression,
        $.try_expression,
      ),

    self: ($) => "self",

    parenthesized_expression: ($) => seq("(", $._expression, ")"),

    tuple_expression: ($) =>
      seq("(", $._expression, ",", commaSep($._expression), optional(","), ")"),

    block_expression: ($) => prec(1, seq("{", repeat1($._statement), "}")),

    // 'try res except (e) { ... }' takes the value a result carried,
    // its arm taking over where an error came back instead; what it
    // unwraps is the result, a call's return or a stored one alike.
    // '?? <fallback>' is the same arm with no error to name, and no arm
    // at all hands the error back to the caller
    try_expression: ($) =>
      seq(
        "try",
        field(
          "result",
          choice($.identifier, $.call_expression, $.field_expression,
                 $.arrow_expression, $.index_expression, $.scoped_identifier,
                 $.generic_reference, $.parenthesized_expression),
        ),
        optional(choice(
          seq("except", "(", field("error", $.identifier), ")",
              field("body", $.block)),
          seq("??", field("fallback", choice($.block, $._expression))),
        )),
      ),

    array_literal: ($) => seq("[", commaSep($._expression), optional(","), "]"),

    // braces in statement position are a block; an aggregate literal is
    // what they mean only where a value is expected
    aggregate_literal: ($) =>
      prec(-1, seq(
        "{",
        commaSep(choice($.aggregate_field, $._expression)),
        optional(","),
        "}",
      )),

    aggregate_field: ($) =>
      seq(field("name", $.identifier), "=", field("value", $._expression)),

    call_expression: ($) =>
      prec(
        PREC.postfix,
        seq(
          field("function", choice($.identifier, $.field_expression, $.scoped_identifier, $.arrow_expression)),
          optional(field("type_arguments", $.type_arguments)),
          field("arguments", $.arguments),
        ),
      ),

    arguments: ($) => seq("(", commaSep($._expression), optional(","), ")"),

    // 'f<i32>' outside a call references the instance
    generic_reference: ($) =>
      prec(
        PREC.postfix,
        seq(
          field("name", choice($.identifier, $.scoped_identifier)),
          field("type_arguments", $.type_arguments),
        ),
      ),

    type_arguments: ($) => seq(token.immediate("<"), commaSep1($.type), ">"),

    // 'A::member', a method reference or an enum member
    // 'A::member', 'a.b::member' through a module binding, 'i64::format'
    // on a primitive: a method reference or an enum member
    scoped_identifier: ($) =>
      prec(
        PREC.postfix,
        seq(
          field(
            "scope",
            choice($.identifier, $.field_expression, $.generic_reference),
          ),
          "::",
          field("name", $.identifier),
        ),
      ),

    field_expression: ($) =>
      prec(PREC.postfix, seq(field("value", $._expression), ".", field("field", $.identifier))),

    arrow_expression: ($) =>
      prec(PREC.postfix, seq(field("value", $._expression), "->", field("field", $.identifier))),

    index_expression: ($) =>
      prec(PREC.postfix, seq(field("value", $._expression), "[", field("index", $._expression), "]")),

    slice_expression: ($) =>
      prec(
        PREC.postfix,
        seq(
          field("value", $._expression),
          "[",
          optional(field("start", $._expression)),
          ":",
          optional(field("stop", $._expression)),
          "]",
        ),
      ),

    unary_expression: ($) =>
      prec.right(
        PREC.unary,
        seq(field("operator", choice("-", "~", "not", "*", "&")), field("operand", $._expression)),
      ),

    cast_expression: ($) =>
      prec.left(PREC.cast, seq(field("value", $._expression), "as", field("type", $.type))),

    binary_expression: ($) => {
      const table = [
        [PREC.or, "or"],
        [PREC.and, "and"],
        [PREC.comparison, choice("<", ">", "<=", ">=", "==", "!=")],
        [PREC.bitor, "|"],
        [PREC.bitxor, "^"],
        [PREC.bitand, "&"],
        [PREC.shift, choice("<<", ">>")],
        [PREC.additive, choice("+", "-")],
        [PREC.multiplicative, choice("*", "/", "%")],
      ];

      return choice(
        ...table.map(([precedence, operator]) =>
          prec.left(
            precedence,
            seq(
              field("left", $._expression),
              field("operator", operator),
              field("right", $._expression),
            ),
          ),
        ),
        // power binds tighter than multiplication and chains right
        prec.right(
          PREC.power,
          seq(field("left", $._expression), field("operator", "**"), field("right", $._expression)),
        ),
      );
    },

    ternary_expression: ($) =>
      prec.right(
        PREC.ternary,
        seq(
          field("condition", $._expression),
          "?",
          field("consequence", $._expression),
          ":",
          field("alternative", $._expression),
        ),
      ),

    // the compile-time functions: '@sizeof(T)', '@typename(v)',
    // '@typeid(v)', '@typeof(v)'
    compile_time_expression: ($) =>
      seq(
        field("name", choice("@sizeof", "@typename", "@typeid", "@typeof")),
        "(",
        field("argument", choice($._expression, $.type)),
        ")",
      ),

    asm_expression: ($) => seq("@asm", optional($.asm_operands), $.asm_string),

    asm_operands: ($) => seq("(", commaSep($._expression), ")"),

    /* ------------------------------------------------------------------ *
     * types
     * ------------------------------------------------------------------ */

    type: ($) =>
      choice(
        $.generic_type,
        $.qualified_type,
        $.identifier,
        $.pointer_type,
        $.array_type,
        $.sized_array_type,
        $.reference_type,
        $.const_type,
        $.function_type,
        $.raw_type,
        $.anonymous_type,
      ),

    // a dotted type name takes its whole chain: 'x as pkg.Type' casts to
    // the qualified type, not to 'pkg' with a field read after it
    qualified_type: ($) =>
      prec.right(2, seq(sep1($.identifier, "."), optional($.type_arguments))),

    generic_type: ($) => prec(1, seq(field("name", $.identifier), $.type_arguments)),

    pointer_type: ($) => prec(PREC.postfix, seq($.type, "*")),
    array_type: ($) => prec(PREC.postfix, seq($.type, "[", "]")),
    sized_array_type: ($) => prec(PREC.postfix, seq($.type, "[", $._expression, "]")),
    reference_type: ($) => prec.right(seq("&", $.type)),
    const_type: ($) => prec.right(seq("const", $.type)),

    // 'fn(A) -> B' takes its return type: the arrow belongs to the type
    function_type: ($) =>
      prec.right(seq(
        "fn",
        "(",
        commaSep($.type),
        ")",
        optional(seq("->", $.type)),
      )),

    raw_type: ($) =>
      prec.right(seq("@raw", $.type_arguments, optional(seq("[", $._expression, "]")))),

    /* ------------------------------------------------------------------ *
     * lexical
     * ------------------------------------------------------------------ */

    identifier: ($) => /[a-zA-Z_]\w*/,

    integer_literal: ($) => token(choice(/\d+/, /0[xX][0-9a-fA-F]+/)),
    float_literal: ($) => token(/\d+\.\d+/),

    boolean_literal: ($) => choice("true", "false"),
    null_literal: ($) => "null",

    string_literal: ($) =>
      seq('"', repeat(choice($.escape_sequence, token.immediate(/[^"\\]+/))), '"'),

    char_literal: ($) =>
      seq("'", choice($.escape_sequence, token.immediate(/[^'\\]/)), "'"),

    escape_sequence: ($) =>
      token.immediate(seq("\\", choice(/[abefnrtv\\'"?]/, /[0-7]{1,3}/, /x[0-9a-fA-F]+/))),

    asm_string: ($) => token(seq('"""', /[^"]*(""?[^"][^"]*)*/, '"""')),

    line_comment: ($) => token(seq("//", /[^\n]*/)),
    block_comment: ($) => token(seq("/*", /[^*]*\*+([^/*][^*]*\*+)*/, "/")),

    type_parameter: ($) =>
      seq(
        field("name", $.identifier),
        optional(seq(":", field("bound", $.type))),
      ),
  },
});

function commaSep(rule) {
  return optional(commaSep1(rule));
}

function commaSep1(rule) {
  return seq(rule, repeat(seq(",", rule)));
}

function sep1(rule, separator) {
  return seq(rule, repeat(seq(separator, rule)));
}
