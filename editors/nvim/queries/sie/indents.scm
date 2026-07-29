; Auto-indent: a brace body indents its contents, the closing brace lines
; up with the line that opened it.
[
  (block)
  (declaration_block)
  (struct_body)
  (extend_body)
  (enum_declaration)
  (case_statement)
  (aggregate_literal)
  (array_literal)
  (parameters)
  (arguments)
] @indent.begin

[
  "}"
  ")"
  "]"
] @indent.branch @indent.end

; an arm's statements sit under it, the next 'when' closing the last
(when_arm) @indent.begin
(else_arm) @indent.begin

(line_comment) @indent.auto
(block_comment) @indent.auto
