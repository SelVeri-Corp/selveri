"""Symbols: AST walk → DocumentSymbol list for the Outline panel."""
from __future__ import annotations

import re
from lsprotocol import types

from selveri.parser import parse_selveri, Decl, If, While, FunctionDecl, Program


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_range(line: int, col: int, end_line: int | None = None, end_col: int | None = None) -> types.Range:
    return types.Range(
        start=types.Position(line=line, character=col),
        end=types.Position(
            line=end_line if end_line is not None else line,
            character=end_col if end_col is not None else col,
        ),
    )


def _find_function_range(source: str, name: str) -> types.Range:
    """Find the line range of a function declaration by searching for its header."""
    lines = source.splitlines()
    start_line = 0
    end_line = len(lines) - 1

    # Find the 'function <name>' line
    func_pattern = re.compile(rf"^\s*function\s+{re.escape(name)}\s*\(")
    end_pattern = re.compile(r"^\s*end\s*$")

    found_start = False
    for i, line in enumerate(lines):
        if not found_start and func_pattern.match(line):
            start_line = i
            found_start = True
        elif found_start and end_pattern.match(line):
            end_line = i
            break

    col = len(lines[start_line]) - len(lines[start_line].lstrip()) if start_line < len(lines) else 0
    return _make_range(start_line, col, end_line, len(lines[end_line]) if end_line < len(lines) else 0)


def _find_decl_range(source: str, name: str) -> types.Range:
    """Find the line where a variable is declared."""
    lines = source.splitlines()
    decl_pattern = re.compile(rf"^\s*{re.escape(name)}\s*:")
    for i, line in enumerate(lines):
        if decl_pattern.match(line):
            col = len(line) - len(line.lstrip())
            return _make_range(i, col, i, len(line))
    return _make_range(0, 0, 0, 0)


def _type_str(type_node: object) -> str:
    return str(type_node)


def _get_local_variable_symbols(stmts: list, source: str) -> list[types.DocumentSymbol]:
    """Collect Variable symbols from a flat statement list (no recursion into nested blocks)."""
    result = []
    for stmt in stmts:
        if isinstance(stmt, Decl):
            r = _find_decl_range(source, stmt.name)
            result.append(
                types.DocumentSymbol(
                    name=stmt.name,
                    kind=types.SymbolKind.Variable,
                    range=r,
                    selection_range=r,
                    detail=_type_str(stmt.type_node),
                )
            )
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_document_symbols(source: str) -> list[types.DocumentSymbol]:
    try:
        ast = parse_selveri(source)
    except Exception:
        return []

    symbols: list[types.DocumentSymbol] = []

    # Function declarations
    for func in ast.func_decls:
        func_range = _find_function_range(source, func.name)
        params_str = ", ".join(
            f"{p.name}: {_type_str(p.type_node)}" for p in (func.params or [])
        )
        ret = _type_str(func.return_type)
        children = _get_local_variable_symbols(func.body, source)
        symbols.append(
            types.DocumentSymbol(
                name=func.name,
                kind=types.SymbolKind.Function,
                range=func_range,
                selection_range=func_range,
                detail=f"({params_str}) -> {ret}",
                children=children,
            )
        )

    # Top-level variable declarations
    for stmt in ast.stmt_seq:
        if isinstance(stmt, Decl):
            r = _find_decl_range(source, stmt.name)
            symbols.append(
                types.DocumentSymbol(
                    name=stmt.name,
                    kind=types.SymbolKind.Variable,
                    range=r,
                    selection_range=r,
                    detail=_type_str(stmt.type_node),
                )
            )

    return symbols
