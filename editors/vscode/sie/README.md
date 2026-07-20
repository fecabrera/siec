# Sie for VS Code

Syntax highlighting for the Sie programming language: keywords, decorators
(`@extern`, `@asm`, ...), builtin types and the prelude names (`Result`,
`Ok`, `Error`, the iteration interfaces), strings with escapes, numbers,
enum members, methods, and `@asm` bodies.

## Install

Copy or link this folder into VS Code's extension directory and reload:

```
ln -s "$(pwd)" ~/.vscode/extensions/sie-lang
```

Files ending in `.sie` then highlight automatically.
