"""Hover: return documentation or type info for the word under the cursor."""
from __future__ import annotations

import re
from lsprotocol import types

from selveri.parser import parse_selveri
from selveri.parser import (
    Decl, If, While, FunctionDecl, Program,
    IntType, FloatType, ListType, DynamicListType,
)

# ---------------------------------------------------------------------------
# Keyword documentation
# ---------------------------------------------------------------------------

KEYWORD_DOCS: dict[str, str] = {
    # Control flow
    "if": (
        "```selveri\nif <condition> then\n    <stmts>\n[else\n    <stmts>]\nfi\n```\n\n"
        "Conditional statement. The `else` branch is optional."
    ),
    "then": "Part of `if <condition> **then** <stmts> fi`.",
    "else": "Optional else-branch of an `if` statement.",
    "fi": "Closes an `if` statement (replaces `end if`).",
    "while": (
        "```selveri\nwhile <condition> do\n    <stmts>\nod\n```\n\n"
        "Loop that runs while the condition is truthy."
    ),
    "do": "Opens the body of a `while` loop.",
    "od": "Closes a `while` loop body (replaces `end while`).",
    # Functions
    "function": (
        "```selveri\nfunction name(param: Type, ...) -> ReturnType ::\n"
        "    <stmts>\n    return <expr>;\nend\n```\n\n"
        "Declares a function. Return value is accessible via `retvar`."
    ),
    "end": "Closes a `function` declaration.",
    "return": "```selveri\nreturn <aexp>;\n```\n\nReturns a value from the current function.",
    "call": (
        "```selveri\ncall functionName(arg1, arg2, ...);\n```\n\n"
        "Calls a function. The return value is stored in `retvar`."
    ),
    # I/O
    "pass": "No-op statement. Useful as an empty branch body.",
    "read": "```selveri\nread <ident>;\n```\n\nReads a value from stdin into the variable.",
    "write": "```selveri\nwrite <ident>;\n```\n\nWrites the value of a variable to stdout.",
    "writeline": (
        "```selveri\nwriteline <ident>;\n```\n\n"
        "Writes the value of a variable to stdout followed by a newline."
    ),
    # Types
    "Int": "**Int** — 32-bit integer type.",
    "Float": "**Float** — double-precision floating-point type.",
    "List": (
        "**List[ElementType, Dimension, size₁, ..., sizeₙ]**\n\n"
        "Multi-dimensional list type. `Dimension` is a positive integer literal. "
        "Sizes can be arithmetic expressions. Example: `List[Int, 2, 3, 4]` is a 3×4 integer matrix.\n\n"
        "For function parameters with variable length: `List[Int, 1]`."
    ),
    # Boolean
    "true": "Boolean literal **true** (evaluates to 1).",
    "false": "Boolean literal **false** (evaluates to 0).",
    "not": "```selveri\nnot <bexp>\n```\n\nLogical NOT.",
    "and": "```selveri\n<bexp> and <bexp>\n```\n\nLogical AND (short-circuit).",
    "or": "```selveri\n<bexp> or <bexp>\n```\n\nLogical OR (short-circuit).",
    "xor": "```selveri\n<bexp> xor <bexp>\n```\n\nLogical XOR.",
    # Special
    "retvar": (
        "**retvar** — special variable that holds the return value of the most "
        "recent `call` statement. Its type matches the called function's return type."
    ),
    "len": (
        "```selveri\nlen(<ident>)\n```\n\n"
        "Returns the number of elements in the **first dimension** of a list. "
        "Returns 0 for non-list variables."
    ),
    "in": "Used in `Forall`/`Exists` quantifiers and in list `for`-style iteration.",
    # Spec temporal operators
    "Forall": (
        "```selveri\n{ Forall <var> [in <domain>] . <spec> };\n```\n\n"
        "Universal quantifier in a specification block. "
        "`domain` can be an interval `[a, b)`, a set `{v1, v2}`, an identifier, or a type."
    ),
    "Exists": (
        "```selveri\n{ Exists <var> [in <domain>] . <spec> };\n```\n\n"
        "Existential quantifier in a specification block."
    ),
    "Always": "**Always** *spec* — temporal operator: *spec* holds at **all** future states.",
    "Eventually": "**Eventually** *spec* — temporal operator: *spec* holds at **some** future state.",
    "Next": "**Next** *spec* — temporal operator: *spec* holds at the **immediately next** state.",
    "Historically": "**Historically** *spec* — temporal operator: *spec* held at **all** past states.",
    "Once": "**Once** *spec* — temporal operator: *spec* held at **some** past state.",
    "Previously": "**Previously** *spec* — temporal operator: *spec* held at the **immediately preceding** state.",
    "Since": "**A Since B** — temporal operator: A has held continuously since B last became true.",
    "Until": "**A Until B** — temporal operator: A holds until B becomes true.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _word_at(source: str, position: types.Position) -> str:
    """Return the identifier/keyword at *position* in *source*."""
    lines = source.splitlines()
    if position.line >= len(lines):
        return ""
    line = lines[position.line]
    col = position.character
    # Expand left
    start = col
    while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
        start -= 1
    # Expand right
    end = col
    while end < len(line) and (line[end].isalnum() or line[end] == "_"):
        end += 1
    return line[start:end]


def _collect_declarations(stmts: list) -> dict[str, str]:
    """Recursively collect all Decl nodes from a statement list."""
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


def _type_str(type_node: object) -> str:
    if isinstance(type_node, IntType):
        return "Int"
    if isinstance(type_node, FloatType):
        return "Float"
    if isinstance(type_node, ListType):
        return str(type_node)
    if isinstance(type_node, DynamicListType):
        return str(type_node)
    return str(type_node)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_hover(source: str, position: types.Position) -> types.Hover | None:
    word = _word_at(source, position)
    if not word:
        return None

    # 1. Keyword / built-in documentation
    if word in KEYWORD_DOCS:
        return types.Hover(
            contents=types.MarkupContent(
                kind=types.MarkupKind.Markdown,
                value=KEYWORD_DOCS[word],
            )
        )

    # 2. Declared variable type
    try:
        ast = parse_selveri(source)
        decls = _collect_all_declarations(ast)
        if word in decls:
            type_label = decls[word]
            return types.Hover(
                contents=types.MarkupContent(
                    kind=types.MarkupKind.Markdown,
                    value=f"```selveri\n{word} : {type_label}\n```",
                )
            )

        # 3. Function name hover — show signature
        for func in ast.func_decls:
            if func.name == word:
                params = ", ".join(
                    f"{p.name}: {_type_str(p.type_node)}" for p in (func.params or [])
                )
                ret = _type_str(func.return_type)
                return types.Hover(
                    contents=types.MarkupContent(
                        kind=types.MarkupKind.Markdown,
                        value=f"```selveri\nfunction {func.name}({params}) -> {ret}\n```",
                    )
                )
    except Exception:
        pass

    return None
