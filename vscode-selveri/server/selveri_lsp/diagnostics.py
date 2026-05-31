"""Diagnostics: current SelVeri parse + compile errors as LSP diagnostics."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse

from lsprotocol import types

from selveri.compiler import compile_selveri_source_to_ir_text
from selveri.diagnostics import (
    Diagnostic,
    DiagnosticFix,
    DiagnosticLabel,
    DiagnosticNote,
    DiagnosticSeverity,
    SourceSpan,
    make_diagnostic,
)
from selveri.errors import SelVeriError
from selveri.parser import collect_raw_specs, parse_selveri
from selveri.spec_parser import parse_spec
from selveri.specs import RawSpecKind


def _path_from_uri(uri_or_path: str | None) -> str | None:
    if not uri_or_path:
        return None
    parsed = urlparse(uri_or_path)
    if parsed.scheme != "file":
        return uri_or_path
    path = unquote(parsed.path)
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return str(Path(path))


def _uri_from_path(source_path: str | None) -> str:
    if not source_path:
        return ""
    if urlparse(source_path).scheme:
        return source_path
    try:
        return Path(source_path).resolve().as_uri()
    except ValueError:
        return source_path


def _position(line: int, column: int) -> types.Position:
    return types.Position(line=max(line - 1, 0), character=max(column - 1, 0))


def _range_from_span(span: SourceSpan | None) -> types.Range:
    if span is None:
        return types.Range(
            start=types.Position(line=0, character=0),
            end=types.Position(line=0, character=1),
        )

    start = _position(span.start_line, span.start_column)
    end = _position(span.end_line, span.end_column)
    if end.line == start.line and end.character <= start.character:
        end = types.Position(line=start.line, character=start.character + 1)
    return types.Range(start=start, end=end)


def _severity(severity: DiagnosticSeverity) -> types.DiagnosticSeverity:
    if severity is DiagnosticSeverity.WARNING:
        return types.DiagnosticSeverity.Warning
    return types.DiagnosticSeverity.Error


def _note_text(note: DiagnosticNote | str) -> str:
    if isinstance(note, DiagnosticNote):
        return f"{note.kind}: {note.message}"
    return f"note: {note}"


def _fix_text(fix: DiagnosticFix) -> str:
    if fix.replacement is None:
        return f"fix: {fix.message}"
    return f"fix: {fix.message} -> {fix.replacement}"


def _message(diag: Diagnostic) -> str:
    title = f"{diag.title}: " if diag.title and diag.title != "error" else ""
    lines = [f"{title}{diag.message}"]
    if diag.hint:
        lines.append(f"hint: {diag.hint}")
    lines.extend(_note_text(note) for note in diag.notes)
    lines.extend(_fix_text(fix) for fix in diag.fixes)
    return "\n".join(lines)


def _related_information(
    labels: Iterable[DiagnosticLabel],
    *,
    fallback_uri: str,
) -> list[types.DiagnosticRelatedInformation]:
    related: list[types.DiagnosticRelatedInformation] = []
    for label in labels:
        message = label.message or label.style
        span = label.span
        related.append(
            types.DiagnosticRelatedInformation(
                location=types.Location(
                    uri=_uri_from_path(span.source.path) or fallback_uri,
                    range=_range_from_span(span),
                ),
                message=message,
            )
        )
    return related


def _to_lsp_diagnostic(diag: Diagnostic, *, document_uri: str) -> types.Diagnostic:
    return types.Diagnostic(
        range=_range_from_span(diag.span),
        message=_message(diag),
        severity=_severity(diag.severity),
        code=diag.code,
        source="selveri",
        related_information=_related_information(
            diag.labels,
            fallback_uri=document_uri,
        ),
    )


def _validate_specs(source: str, source_path: str | None) -> Diagnostic | None:
    program = parse_selveri(source, source_path)
    for raw_spec in collect_raw_specs(program):
        if raw_spec.kind in (RawSpecKind.SPEC_START, RawSpecKind.SPEC_END):
            continue
        try:
            parse_spec(raw_spec.formula_text or "")
        except SelVeriError as exc:
            diag = exc.diagnostic
            if diag.span is not None:
                return diag
            if raw_spec.location is None:
                return diag
            return make_diagnostic(
                category=diag.category,
                title=diag.title,
                message=diag.message,
                span=raw_spec.location,
                code=diag.code,
                severity=diag.severity,
                notes=diag.notes,
                hint=diag.hint,
                labels=(
                    DiagnosticLabel(
                        raw_spec.location,
                        "specification could not be parsed",
                        "primary",
                    ),
                ),
                fixes=diag.fixes,
                context=diag.context,
                counterexample=diag.counterexample,
            )
    return None


def get_diagnostics(source: str, source_path: str | None = None) -> list[types.Diagnostic]:
    """Return parse/compile diagnostics for SelVeri source.

    This intentionally stops before interpretation/verification so editing a
    document never executes user code or waits for input.
    """
    path = _path_from_uri(source_path)
    document_uri = _uri_from_path(source_path) if source_path else ""
    try:
        parse_selveri(source, path)
        spec_diag = _validate_specs(source, path)
        if spec_diag is not None:
            return [_to_lsp_diagnostic(spec_diag, document_uri=document_uri)]
        compile_selveri_source_to_ir_text(source, path)
    except SelVeriError as exc:
        return [_to_lsp_diagnostic(exc.diagnostic, document_uri=document_uri)]
    return []
