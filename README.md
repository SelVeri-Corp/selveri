# SelVeri : Self-Verifying Programming Language

SelVeri is an **educational, imperative** programming language designed to bridge the gap between sequential programming and formal methods.

Unlike traditional languages that rely on runtime errors or unit tests to catch bugs, SelVeri employs _Dynamic Verification_ by utilizing its **First-Order Logic Runtime Engine**. SelVeri integrates the Z3 SMT Solver directly into its runtime. This allows developers to embed complex mathematical and logical specifications—including quantifiers ($\forall, \exists$) and logical implications—which are verified in real-time as the program executes.

## User Manual

For language syntax, specification annotations, verification features, and usage examples, refer to the [SelVeri User Manual](doc/UserManual/SelVeri_User_Manual.pdf).

## Source layout

Python package root: `src/selveri/`

```
src/selveri/
├── __init__.py
├── __main__.py              # python -m selveri
├── cli.py                   # CLI entry (parse → compile → run)
│
├── common/                  # shared infrastructure
│   ├── diagnostics.py       # source spans, rendered errors
│   ├── errors.py            # SelVeriError hierarchy
│   ├── runtime.py           # State, Scope, DeclType
│   └── types.py             # shared scalars (e.g. real = float)
│
├── high_level/              # .svi source language
│   ├── ast.py               # HL AST nodes
│   ├── parser.py            # Lark parser, parse_selveri
│   ├── preprocessor.py      # {spec} extraction
│   └── grammars/
│       └── grammar.lark
│
├── spec/                    # specification / annotation language
│   ├── models.py            # Spec, RawSpec, domains
│   ├── parser.py            # parse_spec
│   └── grammars/
│       └── spec_grammar.lark
│
├── compiler/                # high-level AST → SelVerIR
│   └── compiler.py
│
├── ir/                      # intermediate representation
│   ├── instr.py             # IRInstr
│   └── text.py              # IR text parse/serialize helpers
│
├── abstract_machine/        # SelVerIR interpreter (VM)
│   ├── config.py            # RuntimeConfiguration
│   └── interpreter.py
│
└── verifier/                # Z3 / LTL verification engine
    ├── engine.py            # VerificationEngine
    ├── z3_mapper.py
    ├── future_mapper.py
    ├── future_automaton.py
    └── models.py            # FutureAutomaton, obligations, …
```

Pipeline flow: `high_level` → `compiler` → `ir` → `abstract_machine`, with `spec` + `verifier` hooked into compile/run, and `common` used throughout.

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
python selveri.py examples/payment_fraud_review.svi
```

After editable install, you can use the command directly:

```
selveri examples/payment_fraud_review.svi
```

By default, IR is **not** written to disk. To generate `.svir` output, pass `--emit-ir`:

```
selveri examples/payment_fraud_review.svi --emit-ir
```

Or choose a custom path:

```
selveri examples/payment_fraud_review.svi --emit-ir --output-ir out/payment_fraud_review.svir
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
code --install-extension selveri-0.9.4.vsix
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
