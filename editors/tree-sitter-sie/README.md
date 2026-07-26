# tree-sitter-sie

A [tree-sitter](https://tree-sitter.github.io) grammar for Sie, with the
queries editors read from it: highlights, folds, indents, and textobjects.

## Build

```
tree-sitter generate      # src/parser.c from grammar.js
tree-sitter parse file.sie
```

`src/parser.c` is committed, so an editor can build the parser without the
tree-sitter CLI:

```
cc -o sie.so -shared -Isrc src/parser.c -Os -fPIC
```

## Using it

- **Neovim**: `editors/nvim` carries the queries and registers this
  directory with nvim-treesitter. See its README.
- **Helix**: `editors/helix/languages.toml` holds the `[[grammar]]` entry,
  and `editors/helix/runtime/queries/sie` the queries in Helix's own
  capture vocabulary.

## What it parses

Every `.sie` file in this repository, checked with:

```
for f in $(find ../../packages ../../sie -name '*.sie'); do
    tree-sitter parse -q "$f" || echo "FAIL $f"
done
```

## Notes on the grammar

The primitive type names are **not reserved**. `opaque` names a field in
zlib's `z_stream`, and `i64` takes methods through `@extend`, so they parse
as ordinary identifiers and the highlight queries pick them out by name.
Making them keywords broke both.

Braces are decided late: `{ name = value` opens both an aggregate literal
and a block holding an assignment, and only the `,` or `;` after it settles
which. The grammar declares that conflict rather than guessing, so both
readings stay alive until one completes.

A dotted name reaches its own type: `x as pkg.Type` casts to the qualified
type rather than reading a field off `pkg`.

### Known limit

A `when` arm naming a *generic* array type (`when List<char>[]:`) parses as
an index into a generic reference instead of a type. The plain forms the
spec documents (`when char[]:`, `when String[]:`, `when i32*:`, `when const
char[]:`) all parse.
