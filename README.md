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
