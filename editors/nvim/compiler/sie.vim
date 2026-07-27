" Compiler plugin for Sie: ':compiler sie' then ':make' fills the quickfix
" list with what the build reported.
"
" Inside a package - anything under a 'package.toml' - ':make' runs
" 'sie build' on it, which assembles the include path from the manifest:
" the package's own sources and every dependency's, resolved from what
" 'sie install' put down. Anywhere else it compiles the current file with
" 'siec', which takes its '-I' directories from the command line and
" nowhere else, so name them there:
"
"     :setlocal makeprg=siec\ -I\ packages/core/src\ %:S
"
" A '[library]' is installed rather than built, so ':make' inside one says
" so; compile a file of it with 'siec' and its '-I' directories instead.
"
" siec locates a diagnostic as '<file> at line <n>: <message>', a warning
" naming itself after the colon, so the two patterns below are all it takes.
" A message carrying no file (a link failure, say) stays in the list as
" plain text, and a build's own progress lines are dropped.

if exists("current_compiler")
  finish
endif
let current_compiler = "sie"

let s:save_cpo = &cpo
set cpo&vim

" the package the edited file belongs to, if any: the nearest manifest
" above it, searched from the file's own directory. The search path is a
" 'path'-style list, so a directory's spaces and commas are escaped
let s:manifest = findfile("package.toml",
      \ escape(expand("%:p:h"), " ,") . ";")

if empty(s:manifest)
  CompilerSet makeprg=siec\ %:S
else
  " quoted for the shell, then escaped again for the option parser, which
  " eats one level on its way in
  execute "CompilerSet makeprg=sie\\ build\\ "
        \ . escape(shellescape(fnamemodify(s:manifest, ":p:h")), " \\|\"")
endif

CompilerSet errorformat=
      \%f\ at\ line\ %l:\ %tarning:\ %m,
      \%f\ at\ line\ %l:\ %m,
      \%-Gbuilding\ %.%#,
      \%-Gbuilt\ %.%#,
      \%-G\ \ %.%#,
      \%f:\ %m

let &cpo = s:save_cpo
unlet s:save_cpo
