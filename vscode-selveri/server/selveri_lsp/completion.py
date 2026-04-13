"""Completion: keywords + declared identifiers → CompletionItem list."""
from __future__ import annotations

from lsprotocol import types

from selveri.parser import parse_selveri, Decl, If, While, FunctionDecl, Program

# ---------------------------------------------------------------------------
# All SelVeri keywords
# ---------------------------------------------------------------------------

_CONTROL_KEYWORDS = ["if", "then", "else", "fi", "while", "do", "od"]
_FUNCTION_KEYWORDS = ["function", "end", "return", "call"]
_IO_KEYWORDS = ["pass", "read", "write", "writeline"]
_TYPE_KEYWORDS = ["Int", "Float", "List"]
_BOOL_KEYWORDS = ["true", "false", "not", "and", "or", "xor"]
_SPEC_KEYWORDS = [
    "Forall", "Exists", "Always", "Eventually", "Next",
    "Historically", "Once", "Previously", "Since", "Until",
]

ALL_KEYWORDS = (
    _CONTROL_KEYWORDS
    + _FUNCTION_KEYWORDS
    + _IO_KEYWORDS
    + _TYPE_KEYWORDS
    + _BOOL_KEYWORDS
    + _SPEC_KEYWORDS
    + ["retvar", "len", "in"]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _type_str(type_node: object) -> str:
    return str(type_node)


def _collect_declarations(stmts: list) -> dict[str, str]:
    result: dict[str, str] = {}
    for stmt in stmts:
        if isinstance(stmt, Decl):
            result[stmt.name] = _type_str(stmt.type_node)
        if isinstance(stmt, If):
            result.update(_collect_declarations(stmt.then_s or []))
            result.update(_collect_declarations(stmt.else_s or []))
        if isinstance(stmt, While):
            result.update(_collect_declarations(stmt.body or []))
    return result


def _collect_all_declarations(ast: Program) -> dict[str, str]:
    result = _collect_declarations(ast.stmt_seq)
    for func in ast.func_decls:
        result.update(_collect_declarations(func.body))
    return result


def _params_snippet(params: list) -> str:
    """Build a snippet string like $1, $2 for function parameters."""
    if not params:
        return ""
    parts = [f"${{{i + 1}:{p.name}}}" for i, p in enumerate(params)]
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_completions(
    source: str, position: types.Position
) -> list[types.CompletionItem]:
    items: list[types.CompletionItem] = []

    # Always include all keywords
    for kw in ALL_KEYWORDS:
        items.append(
            types.CompletionItem(
                label=kw,
                kind=types.CompletionItemKind.Keyword,
            )
        )

    # If the source parses, add declared variables and functions
    try:
        ast = parse_selveri(source)

        # Variables
        decls = _collect_all_declarations(ast)
        for name, type_label in decls.items():
            items.append(
                types.CompletionItem(
                    label=name,
                    kind=types.CompletionItemKind.Variable,
                    detail=type_label,
                )
            )

        # Functions — offer a "call funcName(...)" snippet
        for func in ast.func_decls:
            params_str = ", ".join(p.name for p in (func.params or []))
            ret = _type_str(func.return_type)
            snippet = f"call {func.name}({_params_snippet(func.params or [])});"
            items.append(
                types.CompletionItem(
                    label=func.name,
                    kind=types.CompletionItemKind.Function,
                    detail=f"({params_str}) -> {ret}",
                    insert_text=snippet,
                    insert_text_format=types.InsertTextFormat.Snippet,
                )
            )
    except Exception:
        # Broken source — only keywords returned
        pass

    return items
