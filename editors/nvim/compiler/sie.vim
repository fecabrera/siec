" Compiler plugin for Sie: ':compiler sie' then ':make' builds the current
" file and fills the quickfix list with what siec reported.
"
" siec locates a diagnostic as '<file> at line <n>: <message>', a warning
" naming itself after the colon, so the two patterns below are all it takes.
" A message carrying no file (a link failure, say) stays in the list as
" plain text.

if exists("current_compiler")
  finish
endif
let current_compiler = "sie"

let s:save_cpo = &cpo
set cpo&vim

CompilerSet makeprg=siec\ %:S
CompilerSet errorformat=
      \%f\ at\ line\ %l:\ %tarning:\ %m,
      \%f\ at\ line\ %l:\ %m,
      \%f:\ %m

let &cpo = s:save_cpo
unlet s:save_cpo
