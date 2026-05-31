from __future__ import annotations

import sys
from pathlib import Path

from lsprotocol import types

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "vscode-selveri" / "server")]

from selveri_lsp.completion import get_completions, get_ir_completions
from selveri_lsp.diagnostics import get_diagnostics
from selveri_lsp.hover import get_hover
from selveri_lsp.symbols import get_document_symbols


def _hover_text(source: str, line: int, character: int) -> str:
    hover = get_hover(source, types.Position(line=line, character=character))
    assert hover is not None
    assert isinstance(hover.contents, types.MarkupContent)
    return hover.contents.value


def test_valid_spec_block_does_not_report_raw_brace_syntax_error() -> None:
    diagnostics = get_diagnostics("x: Int;\nx := 1;\n{ x = 1 };\n")
    assert diagnostics == []


def test_unicode_greek_identifiers_and_quantifiers_are_valid() -> None:
    diagnostics = get_diagnostics("φ: Int;\nφ := 1;\n{ ∀ α in [0...1] . &α >= 0 };\n")
    assert diagnostics == []


def test_malformed_spec_reports_spec_span() -> None:
    diagnostics = get_diagnostics("x: Int;\n{ x = };\n")
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert "specification" in diagnostic.message
    assert diagnostic.range.start.line == 1
    assert diagnostic.range.start.character == 0


def test_compile_type_mismatch_uses_expression_range() -> None:
    diagnostics = get_diagnostics("x: Int;\nx := 1.5;\n")
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert "type mismatch" in diagnostic.message
    assert diagnostic.range.start.line == 1
    assert diagnostic.range.start.character > 0


def test_hover_current_keywords_and_declarations() -> None:
    source = "x: Int;\nx := read();\nwrite(x);\n{ Forall i in [0...1] . x >= 0 };\n"
    assert "read()" in _hover_text(source, 1, 6)
    assert "write(expr)" in _hover_text(source, 2, 1)
    assert "Universal quantifier" in _hover_text(source, 3, 3)
    assert "x: Int" in _hover_text(source, 0, 0)

    unicode_source = "{ ∀ i in [0...1] . ∃ x in Int . &x >= &i };\n"
    assert "Universal quantifier" in _hover_text(unicode_source, 0, 2)
    assert "Existential quantifier" in _hover_text(unicode_source, 0, 19)

    obtain_source = "x: Int;\nx := obtain(&w, { Exists w in Int . &w > 0 });\n"
    assert "obtain" in _hover_text(obtain_source, 1, 6)


def test_completions_include_current_builtins_and_symbols() -> None:
    source = "function inc(v: Int) -> Int ::\nreturn v + 1;\nend\nx: Int;\n"
    labels = {item.label for item in get_completions(source, types.Position(line=3, character=0))}
    assert {"obtain", "read", "write", "writeline", "Forall", "Exists"} <= labels
    assert {"\\forall", "\\exists", "\\phi"} <= labels
    assert {"x", "v", "inc"} <= labels


def test_ir_completions_include_instructions_types_labels_and_variables() -> None:
    source = "0: DECL INT, x\n1: PUSH 1\n2: STORE x\n3: JZ 8\n8: NOOP\n"
    labels = {item.label for item in get_ir_completions(source, types.Position(line=3, character=6))}
    assert {"DECL", "PUSH", "STORE", "JZ", "NOOP"} <= labels
    assert {"INT", "FLOAT", "LIST[INT]", "LIST[FLOAT]"} <= labels
    assert {"decl-int", "jump-zero"} <= labels
    assert {"0", "8", "x"} <= labels


def test_document_symbols_include_functions_params_and_top_level_decls() -> None:
    source = "function inc(v: Int) -> Int ::\nlocal: Int;\nreturn v + 1;\nend\nx: Int;\n"
    symbols = get_document_symbols(source)
    names = [symbol.name for symbol in symbols]
    assert names == ["inc", "x"]
    assert symbols[0].children is not None
    child_names = [child.name for child in symbols[0].children]
    assert {"v", "local"} <= set(child_names)
