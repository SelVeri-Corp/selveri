"""Completion: current SelVeri keywords, snippets, and declared symbols."""
from __future__ import annotations

import re

from lsprotocol import types

from selveri.high_level.ast import (
    Decl,
    If,
    Program,
    TypeDynamicList,
    TypeInt,
    TypeList,
    TypeReal,
    While,
)
from selveri.high_level.parser import parse_selveri


KEYWORDS = [
    "if",
    "then",
    "else",
    "fi",
    "while",
    "do",
    "od",
    "function",
    "end",
    "return",
    "call",
    "pass",
    "Int",
    "Real",
    "ℤ",
    "ℝ",
    "List",
    "true",
    "false",
    "not",
    "and",
    "or",
    "xor",
    "retvar",
    "in",
    "Forall",
    "Exists",
    "Always",
    "Eventually",
    "Next",
    "Historically",
    "Once",
    "Previously",
    "Since",
    "Until",
    "Var",
]

BUILTINS = ["read", "write", "writeline", "len", "obtain"]

UNICODE_ALIASES = {
    "\\forall": "∀",
    "\\exists": "∃",
    "\\in": "∈",
    "\\alpha": "α",
    "\\beta": "β",
    "\\gamma": "γ",
    "\\delta": "δ",
    "\\epsilon": "ε",
    "\\varepsilon": "ϵ",
    "\\zeta": "ζ",
    "\\eta": "η",
    "\\theta": "θ",
    "\\vartheta": "ϑ",
    "\\iota": "ι",
    "\\kappa": "κ",
    "\\varkappa": "ϰ",
    "\\lambda": "λ",
    "\\mu": "μ",
    "\\nu": "ν",
    "\\xi": "ξ",
    "\\omicron": "ο",
    "\\pi": "π",
    "\\varpi": "ϖ",
    "\\rho": "ρ",
    "\\varrho": "ϱ",
    "\\sigma": "σ",
    "\\varsigma": "ς",
    "\\tau": "τ",
    "\\upsilon": "υ",
    "\\phi": "φ",
    "\\varphi": "ϕ",
    "\\chi": "χ",
    "\\psi": "ψ",
    "\\omega": "ω",
    "\\Alpha": "Α",
    "\\Beta": "Β",
    "\\Gamma": "Γ",
    "\\Delta": "Δ",
    "\\Epsilon": "Ε",
    "\\Zeta": "Ζ",
    "\\Eta": "Η",
    "\\Theta": "Θ",
    "\\Iota": "Ι",
    "\\Kappa": "Κ",
    "\\Lambda": "Λ",
    "\\Mu": "Μ",
    "\\Nu": "Ν",
    "\\Xi": "Ξ",
    "\\Omicron": "Ο",
    "\\Pi": "Π",
    "\\Rho": "Ρ",
    "\\Sigma": "Σ",
    "\\Tau": "Τ",
    "\\Upsilon": "Υ",
    "\\Phi": "Φ",
    "\\Chi": "Χ",
    "\\Psi": "Ψ",
    "\\Omega": "Ω",
    "\\integer": "ℤ",
    "\\real": "ℝ",
}

SNIPPETS: list[tuple[str, str, str]] = [
    ("if", "if ${1:condition} then\n\t${2:pass;}\nfi", "if statement"),
    ("ifelse", "if ${1:condition} then\n\t${2:pass;}\nelse\n\t${3:pass;}\nfi", "if/else statement"),
    ("while", "while ${1:condition} do\n\t${2:pass;}\nod", "while loop"),
    ("function", "function ${1:name}(${2}) -> ${3:Int} ::\n\t${4:return 0;}\nend", "function declaration"),
    ("list", "List[${1:Int}, ${2:1}, ${3:size}]", "static list type"),
    ("dynlist", "List[${1:Int}, 1]", "rank-1 dynamic list parameter"),
    ("spec", "{ ${1:condition} };", "specification block"),
    ("named spec", "{ ${1:name} := ${2:condition} };", "named specification"),
    ("start spec", "{ start ${1:name} };", "named temporal start marker"),
    ("end spec", "{ end ${1:name} };", "named temporal end marker"),
    ("forall", "{ Forall ${1:i} in ${2:[0...n]} . ${3:condition} };", "universal specification"),
    ("exists", "{ Exists ${1:x} in ${2:Int} . ${3:condition} };", "existential specification"),
    ("obtain", "obtain(&${1:w}, Exists ${1:w} in ${2:Int} . ${3:&${1:w} > 0})", "obtain witness expression"),
    ("read", "read()", "read expression"),
    ("write", "write(${1:expr});", "write statement"),
    ("writeline", "writeline(${1:expr});", "writeline statement"),
]

IR_INSTRUCTIONS: list[tuple[str, str]] = [
    ("DECL", "Declare a scalar or list variable"),
    ("LDECL", "Declare a runtime-sized list"),
    ("PUSH", "Push a literal value onto the stack"),
    ("LOAD", "Push a variable value onto the stack"),
    ("STORE", "Pop and store into a variable"),
    ("LLOAD", "Load a list element by index"),
    ("LSTORE", "Store a list element by index"),
    ("LEN", "Push a list length"),
    ("ADD", "Add two stack values"),
    ("SUB", "Subtract two stack values"),
    ("MUL", "Multiply two stack values"),
    ("iDIV", "Integer division"),
    ("fDIV", "Floating-point division"),
    ("EQ", "Equality comparison"),
    ("LT", "Less-than comparison"),
    ("LE", "Less-than-or-equal comparison"),
    ("GT", "Greater-than comparison"),
    ("GE", "Greater-than-or-equal comparison"),
    ("NEG", "Numeric negation"),
    ("AND", "Logical AND"),
    ("OR", "Logical OR"),
    ("XOR", "Logical XOR"),
    ("JZ", "Jump if the popped value is zero"),
    ("GOTO", "Unconditional jump"),
    ("CALL", "Call a function label or name"),
    ("CSCOPE", "Enter a child runtime scope"),
    ("PSCOPE", "Leave the current runtime scope"),
    ("FUNCENV", "Enter a function runtime environment"),
    ("RET", "Return from the current function"),
    ("WRITE", "Write a scalar value"),
    ("WRITELN", "Write a scalar value and newline"),
    ("LWRITE", "Write a list value"),
    ("LWRITELN", "Write a list value and newline"),
    ("READ", "Read a value from stdin"),
    ("VERI", "Verify a specification"),
    ("SPEC_START", "Mark the start of a named temporal specification"),
    ("SPEC_END", "Mark the end of a named temporal specification"),
    ("VERIP", "Verify a parsed/raw specification payload"),
    ("OBT", "Obtain a witness for an existential specification"),
    ("STEP", "Advance verifier execution history"),
    ("NOOP", "No operation"),
]

IR_TYPES = ["INT", "FLOAT", "LIST[INT]", "LIST[FLOAT]"]

IR_SNIPPETS: list[tuple[str, str, str]] = [
    ("decl-int", "DECL INT, ${1:name}", "declare integer"),
    ("decl-float", "DECL FLOAT, ${1:name}", "declare float"),
    ("decl-list-int", "DECL LIST[INT, ${1:size}], ${2:name}", "declare integer list"),
    ("push", "PUSH ${1:value}", "push literal"),
    ("load", "LOAD ${1:name}", "load variable"),
    ("store", "STORE ${1:name}", "store variable"),
    ("jump-zero", "JZ ${1:label}", "conditional jump"),
    ("goto", "GOTO ${1:label}", "unconditional jump"),
    ("verify", "VERI ${1:id}, '${2:spec}'", "verify specification"),
    ("funcenv", "FUNCENV ${1:name}, ${2:INT}", "function environment"),
]

_IR_LABEL_RE = re.compile(r"^\s*(\d+)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
_IR_DECL_RE = re.compile(r"^\s*\d+\s*:\s*(?:DECL|LDECL)\s+[^,]+,\s*([^\s,]+)", re.MULTILINE)
_IR_FUNCENV_RE = re.compile(r"^\s*\d+\s*:\s*FUNCENV\s+([^,\s]+)", re.MULTILINE)


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


def _params_snippet(params: list) -> str:
    return ", ".join(f"${{{i + 1}:{p.name}}}" for i, p in enumerate(params))


def _keyword_items() -> list[types.CompletionItem]:
    return [
        types.CompletionItem(label=kw, kind=types.CompletionItemKind.Keyword)
        for kw in KEYWORDS
    ]


def _builtin_items() -> list[types.CompletionItem]:
    return [
        types.CompletionItem(label=name, kind=types.CompletionItemKind.Function)
        for name in BUILTINS
    ]


def _snippet_items() -> list[types.CompletionItem]:
    return [
        types.CompletionItem(
            label=label,
            kind=types.CompletionItemKind.Snippet,
            detail=detail,
            insert_text=insert_text,
            insert_text_format=types.InsertTextFormat.Snippet,
        )
        for label, insert_text, detail in SNIPPETS
    ]


def _unicode_alias_items() -> list[types.CompletionItem]:
    return [
        types.CompletionItem(
            label=alias,
            kind=types.CompletionItemKind.Text,
            detail=f"Unicode alias: {char}",
            insert_text=char,
            sort_text=f"0{alias}",
        )
        for alias, char in UNICODE_ALIASES.items()
    ]


def _ir_instruction_items() -> list[types.CompletionItem]:
    return [
        types.CompletionItem(
            label=name,
            kind=types.CompletionItemKind.Keyword,
            detail=detail,
        )
        for name, detail in IR_INSTRUCTIONS
    ]


def _ir_type_items() -> list[types.CompletionItem]:
    return [
        types.CompletionItem(label=name, kind=types.CompletionItemKind.TypeParameter)
        for name in IR_TYPES
    ]


def _ir_snippet_items() -> list[types.CompletionItem]:
    return [
        types.CompletionItem(
            label=label,
            kind=types.CompletionItemKind.Snippet,
            detail=detail,
            insert_text=insert_text,
            insert_text_format=types.InsertTextFormat.Snippet,
        )
        for label, insert_text, detail in IR_SNIPPETS
    ]


def get_completions(source: str, position: types.Position) -> list[types.CompletionItem]:
    items = _unicode_alias_items() + _keyword_items() + _builtin_items() + _snippet_items()

    try:
        ast = parse_selveri(source)
    except Exception:
        return items

    for name, type_label in _collect_all_declarations(ast).items():
        items.append(
            types.CompletionItem(
                label=name,
                kind=types.CompletionItemKind.Variable,
                detail=type_label,
            )
        )

    for func in ast.func_decls:
        params = ", ".join(f"{p.name}: {_type_str(p.type_node)}" for p in func.params)
        items.append(
            types.CompletionItem(
                label=func.name,
                kind=types.CompletionItemKind.Function,
                detail=f"({params}) -> {_type_str(func.return_type)}",
                insert_text=f"call {func.name}({_params_snippet(func.params)});",
                insert_text_format=types.InsertTextFormat.Snippet,
            )
        )

    return items


def get_ir_completions(source: str, position: types.Position) -> list[types.CompletionItem]:
    items = _ir_instruction_items() + _ir_type_items() + _ir_snippet_items()

    for label, op in _IR_LABEL_RE.findall(source):
        items.append(
            types.CompletionItem(
                label=label,
                kind=types.CompletionItemKind.Reference,
                detail=f"IR label for {op}",
            )
        )

    for name in sorted(set(_IR_DECL_RE.findall(source))):
        items.append(
            types.CompletionItem(
                label=name,
                kind=types.CompletionItemKind.Variable,
                detail="IR variable",
            )
        )

    for name in sorted(set(_IR_FUNCENV_RE.findall(source))):
        items.append(
            types.CompletionItem(
                label=name,
                kind=types.CompletionItemKind.Function,
                detail="IR function",
            )
        )

    return items
