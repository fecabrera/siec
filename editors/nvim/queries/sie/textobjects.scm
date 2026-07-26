; Selections: 'af'/'if' over a function, 'ac'/'ic' over a type, and the
; usual parameter, call, loop, conditional, comment, and block objects.
(function_declaration) @function.outer
(function_declaration body: (block) @function.inner)
(macro_declaration) @function.outer
(macro_declaration body: (block) @function.inner)

(struct_declaration) @class.outer
(struct_declaration body: (struct_body) @class.inner)
(enum_declaration) @class.outer

(parameter) @parameter.outer
(parameter name: (identifier) @parameter.inner)
(arguments (_) @parameter.outer @parameter.inner)

(call_expression) @call.outer
(call_expression arguments: (arguments) @call.inner)

[
  (while_statement)
  (for_statement)
  (foreach_statement)
] @loop.outer

(while_statement body: (_) @loop.inner)
(for_statement body: (_) @loop.inner)
(foreach_statement body: (_) @loop.inner)

(if_statement) @conditional.outer
(if_statement consequence: (_) @conditional.inner)
(case_statement) @conditional.outer

[
  (line_comment)
  (block_comment)
] @comment.outer

(block) @block.outer
