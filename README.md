# SelVeri : Self-Verifying Programming Language

SelVeri is an **educational, imperative** programming language designed to bridge the gap between sequential programming and formal methods.

Unlike traditional languages that rely on runtime errors or unit tests to catch bugs, SelVeri employs _Dynamic Verification_ by utilizing its **First-Order Logic Runtime Engine**. SelVeri integrates the Z3 SMT Solver directly into its runtime. This allows developers to embed complex mathematical and logical specifications—including quantifiers ($\forall, \exists$) and logical implications—which are verified in real-time as the program executes.

## Setup

Create a Virtual Environment (Optional):

```
python -m venv .venv
.\.venv\Scripts\activate (for Windows)
source .\.venv\Scripts\activate (for Linux/MacOS)
```

Install dependencies:

```
pip install -r requirements.txt
```

Or install the project in editable mode to enable the `selveri` command:

```
pip install -e .
```

### Future LTL (fLTL) and MONA

Specs using temporal operators such as **Eventually**, **Always**, and **Until** are verified by compiling an automaton through [MONA](https://www.brics.dk/mona/). The `mona` executable must be on your `PATH`; otherwise SelVeri raises a verification error for those specs.

**Linux (Debian/Ubuntu)** — MONA is packaged as `mona` in the universe repository:

```
sudo apt update
sudo apt install mona
```

**macOS / Windows** — Install from the [official MONA page](https://www.brics.dk/mona/), or use a Linux environment (**WSL** on Windows, then follow the Debian/Ubuntu steps).

To confirm:

```
mona -h
```

## Run Full Pipeline

Run parser -> compiler -> interpreter from the project root:

```
python selveri.py examples/example_loop.svi
```

After editable install, you can use the command directly:

```
selveri examples/example_loop.svi
```

By default, IR is **not** written to disk. To generate `.svir` output, pass `--emit-ir`:

```
selveri examples/example_loop.svi --emit-ir
```

Or choose a custom path:

```
selveri examples/example_loop.svi --emit-ir --output-ir out/example_loop.svir
```

## Error Diagnostics

SelVeri reports parser, preprocessor, compiler, runtime, and verification
failures as structured diagnostics with source spans, labels, hints, and
counterexamples when available.

See [docs/errors.md](docs/errors.md) for diagnostic codes, examples, and
guidance for adding new errors.

# Selveri VsCode Extension

## Prerequisites

The `selveri` Python package and LSP dependencies must be installed in the interpreter used by the extension:

```bash
pip install -e <path_to_selveri>
pip install -r vscode-selveri/server/requirements.txt
```

## Build & Install

```bash
cd vscode-selveri
npm install
npm run compile
npx vsce package
code --install-extension selveri-0.9.0.vsix
```

## Development

1. Open the `vscode-selveri` folder in VS Code.
2. Open `client/src/extension.ts`.
3. Press F5 to launch an Extension Development Host.

Example `.vscode/launch.json`:

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
