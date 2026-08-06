# Sie for VS Code

Syntax highlighting for the Sie programming language: keywords, decorators
(`@extern`, `@asm`, ...), builtin types and the prelude names (`Result`,
`Ok`, `Error`, the iteration and operator interfaces, including `GetItem`
and `SetItem`), strings with escapes, numbers, enum members, methods, and
`@asm` bodies.

Indentation follows brace bodies and aligns multiline parenthesized or
bracketed lists under their opening delimiter. After a continued function
declaration ends in `);`, the next line returns to the declaration's
indentation.

With the `sie-lsp` language server it also serves diagnostics as you
type, completion, the document outline, hover, and go-to-definition,
compiled and typed by the real compiler front end. Completion includes
locals, visible declarations, builtins, keywords, imported modules, and a
module's public exports after a dot (`import util; util.`). Branches rejected
by compile-time `@if` conditions are dimmed through semantic highlighting.

## Install

Install the server next to the compiler:

```
pip install -e '.[lsp]'
```

Then install the client's one dependency, and copy or link this folder
into VS Code's extension directory:

```
npm install
ln -s "$(pwd)" ~/.vscode/extensions/sie-lang
```

Reload VS Code and open a `.sie` file. (Packaging a `.vsix` with
`vsce package` works too, once `npm install` has run.)

## Settings

- `sie.serverPath` — command that launches the server (default
  `sie-lsp`); point it at an absolute path (a virtualenv's, say) when
  it isn't on VS Code's PATH.
- `sie.includePaths` — extra include directories for analysis, like the
  compiler's `-I`. The project's `package.toml` supplies the rest on its
  own: its `[package] include` entries, and, where it declares an `[app]`
  or `[library]`, that package's sources and every dependency's resolved
  from what `sie install` put down.
