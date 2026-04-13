# SelVeri VSCode Extension

Language support for **SelVeri** (`.svi` files) — syntax highlighting, real-time diagnostics, hover documentation, code completion, and document symbols.

## Features

- **Syntax highlighting** — keywords, types, operators, spec blocks (`{ ... };`), temporal logic operators
- **Diagnostics** — parse and compiler errors appear as red squiggles in the editor and in the Problems panel
- **Hover** — documentation for all keywords, temporal operators, and type info for declared variables
- **Completion** — all SelVeri keywords, declared variables, and function call snippets
- **Document Symbols** — functions and top-level variables in the Outline panel

## Prerequisites

The `selveri` Python package must be installed in the Python interpreter used by the extension:

```bash
pip install -e /path/to/selveri
pip install -r vscode-selveri/server/requirements.txt
```

## Build & Install

```bash
cd vscode-selveri

# Install Node dependencies
npm install

# Compile TypeScript client
npm run compile

# (Optional) Package as VSIX
npx vsce package

# Install the VSIX
code --install-extension selveri-0.1.0.vsix
```

## Development (F5 debug launch)

1. Open the `selveri` repository root in VSCode
2. Open `vscode-selveri/client/src/extension.ts`
3. Press **F5** — this opens an Extension Development Host with the extension loaded

You will need a `.vscode/launch.json` inside `vscode-selveri/`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Launch Extension",
      "type": "extensionHost",
      "request": "launch",
      "args": ["--extensionDevelopmentPath=${workspaceFolder}"],
      "outFiles": ["${workspaceFolder}/client/out/**/*.js"]
    }
  ]
}
```

## Settings

| Setting | Default | Description |
|---|---|---|
| `selveri.pythonPath` | `"python"` | Path to Python interpreter with `selveri` installed |
| `selveri.trace.server` | `"off"` | LSP trace verbosity (`off` / `messages` / `verbose`) |

## Commands

| Command | Description |
|---|---|
| `SelVeri: Run File` | Run the active `.svi` file in a terminal |
| `SelVeri: Compile to IR` | Compile and print the SelVerIR for the active file |
| `SelVeri: Restart Language Server` | Restart the Python language server process |
