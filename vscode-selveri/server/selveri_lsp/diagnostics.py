"""Diagnostics: parse + compile SelVeri source → LSP Diagnostic list."""
from __future__ import annotations

from lsprotocol import types
from lark import UnexpectedToken, UnexpectedCharacters, UnexpectedEOF

from selveri.parser import SELVERI_PARSER
from selveri.errors import CompilerError, ParserError

# The compiler may not be importable if the package is in an intermediate state
# (e.g., it references parser symbols that haven't been added yet).
try:
    from selveri.compiler import compile_selveri_source_to_ir_text as _compile
    _COMPILER_AVAILABLE = True
except ImportError:
    _COMPILER_AVAILABLE = False
    _compile = None  # type: ignore[assignment]


def _make_diag(
    start_line: int,
    start_col: int,
    end_line: int,
    end_col: int,
    message: str,
    severity: types.DiagnosticSeverity = types.DiagnosticSeverity.Error,
) -> types.Diagnostic:
    return types.Diagnostic(
        range=types.Range(
            start=types.Position(line=start_line, character=start_col),
            end=types.Position(line=end_line, character=end_col),
        ),
        message=message,
        severity=severity,
        source="selveri",
    )


def get_diagnostics(source: str) -> list[types.Diagnostic]:
    """Return LSP diagnostics for *source* by running the parser then compiler."""
    # --- Step 1: Parse using raw Lark so we get precise line/col from exceptions ---
    try:
        SELVERI_PARSER.parse(source)
    except UnexpectedToken as e:
        line = (e.line or 1) - 1
        col = (e.column or 1) - 1
        token_str = str(e.token) if e.token else "?"
        msg = f"Syntax error: unexpected token '{token_str}'"
        expected = getattr(e, "expected", None)
        if expected:
            readable = [_readable_terminal(t) for t in sorted(expected) if not t.startswith("__")]
            if readable:
                msg += f". Expected: {', '.join(readable[:6])}"
        return [_make_diag(line, col, line, col + len(token_str), msg)]

    except UnexpectedCharacters as e:
        line = (e.line or 1) - 1
        col = (e.column or 1) - 1
        char = getattr(e, "char", "?")
        msg = f"Syntax error: unexpected character '{char}'"
        return [_make_diag(line, col, line, col + 1, msg)]

    except UnexpectedEOF as e:
        # Point to the last line
        lines = source.splitlines()
        last_line = max(len(lines) - 1, 0)
        last_col = len(lines[-1]) if lines else 0
        expected = getattr(e, "expected", None)
        msg = "Syntax error: unexpected end of file"
        if expected:
            readable = [_readable_terminal(t) for t in sorted(expected) if not t.startswith("__")]
            if readable:
                msg += f". Expected: {', '.join(readable[:6])}"
        return [_make_diag(last_line, last_col, last_line, last_col, msg)]

    # --- Step 2: Compile to catch type / scope errors ---
    if _COMPILER_AVAILABLE and _compile is not None:
        try:
            _compile(source)
        except CompilerError as e:
            return [_make_diag(0, 0, 0, 1, str(e))]
        except ParserError as e:
            return [_make_diag(0, 0, 0, 1, str(e))]

    return []


def _readable_terminal(terminal: str) -> str:
    """Convert Lark terminal names to human-readable strings."""
    _MAP = {
        "IDENT": "identifier",
        "INT_LIT": "integer literal",
        "FLOAT_LIT": "float literal",
        "COMP_OP": "comparison operator (=, <, <=, >, >=)",
        "LPAR": "(",
        "RPAR": ")",
        "LSQB": "[",
        "RSQB": "]",
        "LBRACE": "{",
        "RBRACE": "}",
        "SEMICOLON": ";",
        "COLON": ":",
        "COMMA": ",",
        "DOT": ".",
    }
    return _MAP.get(terminal, terminal.lower().replace("_", " "))
