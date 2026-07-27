# Sie for Neovim

Syntax highlighting, filetype settings, quickfix integration, and the
`sie-lsp` language server for `.sie` files.

## Install

The directory is a plugin as it stands: point a plugin manager at it, or
copy its contents into `~/.config/nvim/`.

lazy.nvim:

```lua
{ dir = "/path/to/sielang/editors/nvim", ft = "sie" }
```

vim.pack (Neovim 0.12+):

```lua
vim.pack.add({
  { src = "file:///path/to/sielang/editors/nvim", name = "sie" },
})
```

packer / vim-plug: give the same path to `use` or `Plug`.

Without a plugin manager:

```
cp -r editors/nvim/* ~/.config/nvim/
```

## What it gives

**Highlighting** covers the language as the compiler reads it: keywords,
the builtin types (`i32`, `opaque`, `char`, ...), the prelude's builtin
declarations (`Result`, `Any`, `Iterator`, the operator interfaces), every
`@directive` from `@const` to `@sizeof`, the target constants, function
declarations with their `S::method` receivers, and the literal forms
including hexadecimal, escapes, and char literals.

**Filetype settings** give `gc` comment toggling (`//`, with `/* */` for
blocks), four-space indentation, and C-style auto-indent. `@` counts as a
word character, so `*`, `K`, and `gd` take `@sizeof` whole.

**Quickfix**: `:compiler sie` then `:make` builds the current file and
fills the quickfix list from what `siec` reported, warnings marked as
warnings.

```vim
:compiler sie
:make
:copen
```

The `-I` include directories a project needs can ride along:

```vim
:compiler sie
:setlocal makeprg=siec\ -I\ packages/core/src\ %:S
```

**The language server** gives diagnostics as you type, hover (`K`),
go-to-definition (`grd`), and the document outline. It ships with the
compiler:

```
pip install -e '.[lsp]'
```

Then, in your Neovim configuration (0.11 or newer):

```lua
vim.lsp.enable("sie")
```

The include path comes from the project's `package.toml`: the nearest one
above the edited file, and the workspace root's. Each contributes its
`[package] include` entries and, where it declares an `[app]` or
`[library]` of its own, that package's sources and every dependency's
resolved from what `sie install` put down. Extra directories, the
compiler's `-I`, go in the initialization options:

```lua
vim.lsp.config("sie", { init_options = { includePaths = { "packages/core/src" } } })
vim.lsp.enable("sie")
```

On Neovim 0.10 and older, register it through `lspconfig` instead:

```lua
require("lspconfig.configs").sie = {
  default_config = {
    cmd = { "sie-lsp" },
    filetypes = { "sie" },
    root_dir = require("lspconfig.util").root_pattern("package.toml", ".git"),
    init_options = { includePaths = {} },
  },
}
require("lspconfig").sie.setup({})
```

Point `cmd` at an absolute path when `sie-lsp` is not on Neovim's `PATH`,
a virtualenv's `bin/sie-lsp` being the usual case.

## Tree-sitter

The Vim syntax file above needs nothing installed and covers the language
token by token. For structural highlighting, folds, and textobjects, the
grammar in `editors/tree-sitter-sie` parses Sie properly.

With nvim-treesitter, register the parser and install it:

```lua
require("sie").setup({ path = "/path/to/sielang/editors/tree-sitter-sie" })
-- then, once: :TSInstall sie
```

By hand, build the parser and drop it on the runtimepath:

```
cd editors/tree-sitter-sie
cc -o ~/.config/nvim/parser/sie.so -shared -Isrc src/parser.c -Os -fPIC
```

Either way the queries in `queries/sie/` are found from this plugin, and
`vim.treesitter.start(0, "sie")` (or nvim-treesitter's `highlight.enable`)
takes over from the Vim syntax file. They give `highlights`, `folds`,
`indents`, and `textobjects` (`af`/`if` over a function, `ac`/`ic` over a
struct, `aa`/`ia` over a parameter).

## Layout

| Path | What it does |
|---|---|
| `ftdetect/sie.lua` | `.sie` files are filetype `sie` |
| `syntax/sie.vim` | the highlighting |
| `ftplugin/sie.lua` | comments, indentation, `iskeyword` |
| `compiler/sie.vim` | `makeprg` and the `errorformat` for `:make` |
| `lsp/sie.lua` | the `sie-lsp` client, for `vim.lsp.enable` |
| `lua/sie/init.lua` | registers the tree-sitter parser with nvim-treesitter |
| `queries/sie/` | tree-sitter highlights, folds, indents, textobjects |
