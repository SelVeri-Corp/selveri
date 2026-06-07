"""Hover: return documentation or type info for the word under the cursor."""
from __future__ import annotations

from lsprotocol import types

from selveri.high_level.ast import (
    Decl,
    FunctionDecl,
    If,
    Program,
    TypeDynamicList,
    TypeInt,
    TypeList,
    TypeReal,
    While,
)
from selveri.high_level.parser import parse_selveri


KEYWORD_DOCS: dict[str, str] = {
    "if": "```selveri\nif <condition> then\n    <stmts>\nelse\n    <stmts>\nfi\n```\n\nConditional statement. The `else` branch is optional.",
    "then": "Starts the body of an `if` statement.",
    "else": "Starts the optional else branch of an `if` statement.",
    "fi": "Closes an `if` statement.",
    "while": "```selveri\nwhile <condition> do\n    <stmts>\nod\n```\n\nLoop that runs while the condition is truthy.",
    "do": "Starts the body of a `while` loop.",
    "od": "Closes a `while` loop.",
    "function": "```selveri\nfunction name(param: Type, ...) -> ReturnType ::\n    <stmts>\n    return <expr>;\nend\n```\n\nDeclares a function.",
    "end": "Closes a function declaration.",
    "return": "```selveri\nreturn <expr>;\n```\n\nReturns a value from the current function.",
    "call": "```selveri\ncall functionName(arg1, arg2);\n```\n\nCalls a function and stores its return value in `retvar`.",
    "pass": "No-op statement. Useful as an empty branch body.",
    "read": "```selveri\nvalue := read();\n```\n\nReads a value from stdin as an arithmetic expression.",
    "write": "```selveri\nwrite(expr);\n```\n\nWrites an expression to stdout.",
    "writeline": "```selveri\nwriteline(expr);\n```\n\nWrites an expression to stdout followed by a newline.",
    "obtain": "```selveri\nx := obtain(&w, Exists w in Int . &w > 0);\n```\n\nFinds a witness for an existential specification and returns it as an arithmetic expression.",
    "Int": "Integer type. Equivalent to `ℤ`.",
    "Real": "Real (floating-point) type. Equivalent to `ℝ`.",
    "ℤ": "Integer type (ℤ). Same as `Int`.",
    "ℝ": "Real (floating-point) type (ℝ). Same as `Real`.",
    "List": "```selveri\nitems: List[Int, 1, len];\nfunction f(xs: List[Int, 1]) -> Int ::\n```\n\nStatic lists include a shape after the dimension. Rank-1 function parameters can omit the shape for variable-length lists.",
    "true": "Boolean literal `true`.",
    "false": "Boolean literal `false`.",
    "not": "Logical NOT: `not <bexp>`.",
    "and": "Logical AND: `<bexp> and <bexp>`.",
    "or": "Logical OR: `<bexp> or <bexp>`.",
    "xor": "Logical XOR: `<bexp> xor <bexp>`.",
    "retvar": "Special variable holding the return value of the most recent `call` statement.",
    "len": "```selveri\nlen(listName)\n```\n\nReturns the first-dimension length of a list.",
    "in": "Introduces a quantifier domain in `Forall` and `Exists` specifications.",
    "Forall": "```selveri\n{ Forall i in [0...len(xs) - 1] . xs[&i] >= 0 };\n```\n\nUniversal quantifier. Domains include ranges, intervals, lists, identifiers, `Int`, `Real`, `ℤ`, `ℝ`, `Var[Int]`, and `Var[Real]`.",
    "Exists": "```selveri\n{ Exists x in [1...10] . &x * &x = 9 };\n```\n\nExistential quantifier.",
    "Always": "Future temporal operator: the formula holds at all future states.",
    "Eventually": "Future temporal operator: the formula holds at some future state.",
    "Next": "Future temporal operator: the formula holds at the next state.",
    "Historically": "Past temporal operator: the formula held at all previous states.",
    "Once": "Past temporal operator: the formula held at some previous state.",
    "Previously": "Past temporal operator: the formula held at the immediately previous state.",
    "Since": "Past temporal operator: `A Since B`.",
    "Until": "Future temporal operator: `A Until B`.",
    "start": "```selveri\n{ start name };\n```\n\nMarks the start of a named temporal specification scope.",
}

KEYWORD_DOCS["∀"] = KEYWORD_DOCS["Forall"]
KEYWORD_DOCS["∃"] = KEYWORD_DOCS["Exists"]


def _word_at(source: str, position: types.Position) -> str:
    lines = source.splitlines()
    if position.line >= len(lines):
        return ""
    line = lines[position.line]
    col = min(position.character, len(line))
    if col > 0 and col == len(line):
        col -= 1
    if col < len(line) and line[col] in "∀∃ℤℝ":
        return line[col]
    if col < len(line) and line[col] == "&":
        col += 1
    start = col
    while start > 0 and (line[start - 1].isalnum() or line[start - 1] in "_&"):
        start -= 1
    end = col
    while end < len(line) and (line[end].isalnum() or line[end] == "_"):
        end += 1
    return line[start:end].lstrip("&")


def _type_str(type_node: object) -> str:
    if isinstance(type_node, TypeInt):
        return "Int"
    if isinstance(type_node, TypeReal):
        return "Real"
    if isinstance(type_node, TypeList):
        shape = ", ".join(str(part) for part in type_node.shape)
        suffix = f", {shape}" if shape else ""
        return f"List[{_type_str(type_node.elem)}, {type_node.dimension.value}{suffix}]"
    if isinstance(type_node, TypeDynamicList):
        shape = ", ".join(str(part) for part in type_node.shape if part is not None)
        suffix = f", {shape}" if shape else ""
        return f"List[{_type_str(type_node.elem)}, {type_node.dimension.value}{suffix}]"
    return str(type_node)


def _collect_declarations(stmts: list) -> dict[str, str]:
    result: dict[str, str] = {}
    for stmt in stmts:
        if isinstance(stmt, Decl):
            result[stmt.name] = _type_str(stmt.type_node)
        elif isinstance(stmt, If):
            result.update(_collect_declarations(stmt.then_s or []))
            result.update(_collect_declarations(stmt.else_s or []))
        elif isinstance(stmt, While):
            result.update(_collect_declarations(stmt.body or []))
    return result


def _collect_all_declarations(ast: Program) -> dict[str, str]:
    result = _collect_declarations(ast.stmt_seq)
    for func in ast.func_decls:
        for param in func.params:
            result[param.name] = _type_str(param.type_node)
        result.update(_collect_declarations(func.body))
    return result


def _function_signature(func: FunctionDecl) -> str:
    params = ", ".join(f"{p.name}: {_type_str(p.type_node)}" for p in func.params)
    return f"function {func.name}({params}) -> {_type_str(func.return_type)}"


def get_hover(source: str, position: types.Position) -> types.Hover | None:
    word = _word_at(source, position)
    if not word:
        return None

    if word in KEYWORD_DOCS:
        return types.Hover(
            contents=types.MarkupContent(
                kind=types.MarkupKind.Markdown,
                value=KEYWORD_DOCS[word],
            )
        )

    try:
        ast = parse_selveri(source)
    except Exception:
        return None

    decls = _collect_all_declarations(ast)
    if word in decls:
        return types.Hover(
            contents=types.MarkupContent(
                kind=types.MarkupKind.Markdown,
                value=f"```selveri\n{word}: {decls[word]}\n```",
            )
        )

    for func in ast.func_decls:
        if func.name == word:
            return types.Hover(
                contents=types.MarkupContent(
                    kind=types.MarkupKind.Markdown,
                    value=f"```selveri\n{_function_signature(func)}\n```",
                )
            )
    return None
