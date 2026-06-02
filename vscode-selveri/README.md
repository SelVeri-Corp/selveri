# SelVeri VS Code Extension

Language support for **SelVeri** (`.svi` files): syntax highlighting, parse/compile diagnostics, hover documentation, completions, and document symbols.

## Features

- Syntax highlighting for current SelVeri syntax, including `{ ... }` specification blocks, named specs, temporal operators, `obtain`, and bound variables such as `&i`.
- Diagnostics powered by the current Python parser and compiler, including source ranges, diagnostic codes, notes, hints, and related spans.
- Hover documentation for keywords, builtins, temporal operators, declarations, parameters, and functions.
- Completion for keywords, builtins, snippets, declared variables, parameters, functions, and Unicode aliases.
- Automatic Unicode input in `.svi` files, for example `\forall` to `∀`, `\exists` to `∃`, and `\phi` to `φ`.
- Document symbols for functions, parameters, and declarations.

## Current Syntax Examples

```selveri
x: Int;
x := read();
writeline(x);

items: List[Int, 1, 5];
{ Forall i in [0...len(items) - 1] . items[&i] >= 0 };
{ ∀ φ in [0...len(items) - 1] . items[&φ] >= 0 };

witness: Int;
witness := obtain(&w, { Exists w in Int . &w > x });

{ start sorted };
{ sorted := Always items[0] <= items[1] };
{ end sorted };
```

Function parameters may use rank-1 dynamic list types:

```selveri
function first(xs: List[Int, 1]) -> Int ::
    return xs[0];
end
```

## Settings

| Setting | Default | Description |
|---|---|---|
| `selveri.pythonPath` | `python` | Path to the Python interpreter with `selveri` installed. |
| `selveri.trace.server` | `off` | LSP trace verbosity: `off`, `messages`, or `verbose`. |

## Commands

| Command | Description |
|---|---|
| `SelVeri: Run File` | Run the active `.svi` file in a terminal. |
| `SelVeri: Compile to IR` | Compile and print SelVerIR for the active file. |
| `SelVeri: Restart Language Server` | Restart the Python language server process. |
