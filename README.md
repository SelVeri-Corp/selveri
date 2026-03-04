# SelVeri : Self-Verifying Programming Language

SelVeri is an **educational, imperative** programming language designed to bridge the gap between sequential programming and formal methods.

Unlike traditional languages that rely on runtime errors or unit tests to catch bugs, SelVeri employs _Dynamic Verification_ by utilizing its **First-Order Logic Runtime Engine**. SelVeri integrates the Z3 SMT Solver directly into its runtime. This allows developers to embed complex mathematical and logical specifications—including quantifiers ($\forall, \exists$) and logical implications—which are verified in real-time as the program executes.

## Parser

In order to run the parser, [Lark](https://github.com/lark-parser/lark) must be installed via

```
pip install lark
```

Then parser code can be tested using the contents of the src/example.sv file.

```
cd src
python parser.py
```
