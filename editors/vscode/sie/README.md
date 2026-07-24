# Sie for VS Code

Syntax highlighting for the Sie programming language: keywords, decorators
(`@extern`, `@asm`, ...), builtin types and the prelude names (`Result`,
`Ok`, `Error`, the iteration interfaces), strings with escapes, numbers,
enum members, methods, and `@asm` bodies.

With the `sie-lsp` language server it also serves diagnostics as you
type, the document outline, hover, and go-to-definition, compiled and
typed by the real compiler front end.

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
  compiler's `-I`. The project's `package.toml` (`[package] include`)
  supplies the rest on its own.
