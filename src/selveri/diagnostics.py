from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import IO, Iterable, Mapping, Sequence


def _split_source_lines(text: str) -> tuple[str, ...]:
    lines = text.splitlines()
    if text.endswith(("\n", "\r")):
        lines.append("")
    return tuple(lines or [""])


@dataclass(frozen=True)
class SourceFile:
    path: str
    text: str
    _lines: tuple[str, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_lines", _split_source_lines(self.text))

    @property
    def display_path(self) -> str:
        return self.path or "<input>"

    def get_line(self, line_number: int) -> str:
        if line_number < 1 or line_number > len(self._lines):
            return ""
        return self._lines[line_number - 1]


@dataclass(frozen=True)
class SourceSpan:
    source: SourceFile
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    start_pos: int
    end_pos: int

    @property
    def file_path(self) -> str:
        return self.source.display_path

    @property
    def source_text(self) -> str:
        return self.source.text

    @property
    def location(self) -> str:
        return f"{self.file_path}:{self.start_line}:{self.start_column}"

    @classmethod
    def from_lark_meta(cls, source: SourceFile, meta: object) -> "SourceSpan":
        return cls(
            source=source,
            start_line=getattr(meta, "line", 1),
            start_column=getattr(meta, "column", 1),
            end_line=getattr(meta, "end_line", getattr(meta, "line", 1)),
            end_column=getattr(meta, "end_column", getattr(meta, "column", 1)),
            start_pos=getattr(meta, "start_pos", 0),
            end_pos=getattr(meta, "end_pos", getattr(meta, "start_pos", 0)),
        )

    @classmethod
    def from_token(cls, source: SourceFile, token: object) -> "SourceSpan":
        value = getattr(token, "value", "")
        start_pos = getattr(token, "start_pos", 0)
        if not hasattr(token, "start_pos") and hasattr(token, "pos_in_stream"):
            start_pos = getattr(token, "pos_in_stream")
        end_pos = getattr(token, "end_pos", start_pos + len(value))
        line = getattr(token, "line", 1)
        column = getattr(token, "column", 1)
        end_line = getattr(token, "end_line", line)
        end_column = getattr(token, "end_column", column + max(len(value), 1))
        return cls(
            source=source,
            start_line=line,
            start_column=column,
            end_line=end_line,
            end_column=end_column,
            start_pos=start_pos,
            end_pos=end_pos,
        )


class DiagnosticSeverity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Diagnostic:
    code: str | None
    severity: DiagnosticSeverity
    category: str
    title: str
    message: str
    span: SourceSpan | None
    notes: tuple[str, ...] = field(default_factory=tuple)
    hint: str | None = None


def make_diagnostic(
    *,
    category: str,
    title: str,
    message: str,
    span: SourceSpan | None = None,
    code: str | None = None,
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
    notes: Sequence[str] = (),
    hint: str | None = None,
) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity=severity,
        category=category,
        title=title,
        message=message,
        span=span,
        notes=tuple(notes),
        hint=hint,
    )


def _display_value(value: str) -> str:
    if value == "":
        return "end of input"
    if value == "\n":
        return "newline"
    return repr(value)


_ANSI_RESET = "\x1b[0m"
_ANSI_BOLD = "\x1b[1m"
_ANSI_DIM = "\x1b[2m"
_ANSI_RED = "\x1b[31m"
_ANSI_YELLOW = "\x1b[33m"
_ANSI_GREEN = "\x1b[32m"
_ANSI_BLUE = "\x1b[34m"
_ANSI_CYAN = "\x1b[36m"


def should_use_color(
    *,
    stream: IO[str] | None = None,
    mode: str = "auto",
    env: Mapping[str, str] | None = None,
) -> bool:
    if mode == "never":
        return False
    if mode == "always":
        return True

    environment = os.environ if env is None else env
    if "NO_COLOR" in environment:
        return False

    force_color = environment.get("FORCE_COLOR")
    if force_color is not None and force_color != "0":
        return True

    term = environment.get("TERM", "")
    if term.lower() == "dumb":
        return False

    if stream is None or not hasattr(stream, "isatty"):
        return False

    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _style(text: str, *codes: str, color: bool) -> str:
    if not color or not text:
        return text
    return "".join(codes) + text + _ANSI_RESET


def _format_header(diag: Diagnostic, *, color: bool) -> str:
    header = f"{diag.category}: {diag.title}"
    if diag.code:
        header = f"{header} [{diag.code}]"
    header_color = _ANSI_YELLOW if diag.severity is DiagnosticSeverity.WARNING else _ANSI_RED
    return _style(header, _ANSI_BOLD, header_color, color=color)


def _format_pointer(span: SourceSpan) -> tuple[str, str, str | None]:
    raw_line = span.source.get_line(span.start_line)
    display_line = raw_line.expandtabs(4)

    start_index = max(span.start_column - 1, 0)
    if span.start_line == span.end_line:
        end_index = max(start_index + 1, span.end_column - 1)
        focus = raw_line[start_index:end_index]
        if not focus and span.end_pos > span.start_pos:
            focus = raw_line[start_index : start_index + max(span.end_pos - span.start_pos, 1)]
    else:
        focus = raw_line[start_index:]
    focus_width = max(1, len(focus.expandtabs(4)))
    pointer = " " * len(raw_line[:start_index].expandtabs(4)) + "^" * focus_width

    continuation = None
    if span.start_line != span.end_line:
        continuation = (
            f"span continues through line {span.end_line}, column {span.end_column}"
        )
    return display_line, pointer, continuation


def render_diagnostic(diag: Diagnostic, *, color: bool = False) -> str:
    lines = [_format_header(diag, color=color)]
    if diag.span is not None:
        span = diag.span
        lines.append(
            _style(" --> ", _ANSI_CYAN, color=color) + _style(span.location, _ANSI_BOLD, color=color)
        )
        lines.append(_style("  |", _ANSI_DIM, color=color))
        line_number = str(span.start_line)
        gutter = " " * len(line_number)
        source_line, pointer, continuation = _format_pointer(span)
        lines.append(
            _style(line_number, _ANSI_CYAN, color=color)
            + _style(" | ", _ANSI_DIM, color=color)
            + source_line
        )
        lines.append(
            gutter
            + _style(" | ", _ANSI_DIM, color=color)
            + _style(pointer, _ANSI_RED, _ANSI_BOLD, color=color)
        )
        if continuation is not None:
            lines.append(
                gutter
                + _style(" | ", _ANSI_DIM, color=color)
                + _style(continuation, _ANSI_DIM, color=color)
            )
    lines.append("")
    lines.append(_style("= ", _ANSI_BOLD, color=color) + diag.message)
    for note in diag.notes:
        lines.append(
            _style("= ", _ANSI_BOLD, color=color)
            + _style("note:", _ANSI_BLUE, _ANSI_BOLD, color=color)
            + f" {note}"
        )
    if diag.hint:
        lines.append(
            _style("= ", _ANSI_BOLD, color=color)
            + _style("hint:", _ANSI_GREEN, _ANSI_BOLD, color=color)
            + f" {diag.hint}"
        )
    return "\n".join(lines)


def render_internal_error(*, color: bool = False) -> str:
    return "\n".join(
        [
            _style(
                "InternalError: unexpected compiler failure",
                _ANSI_BOLD,
                _ANSI_RED,
                color=color,
            ),
            "",
            _style("= ", _ANSI_BOLD, color=color)
            + "Please rerun with --debug to see the Python traceback.",
        ]
    )


_FRIENDLY_TERMINALS = {
    "IDENT": "identifier",
    "INT_LIT": "integer literal",
    "FLOAT_LIT": "float literal",
    "LPAR": "'('",
    "RPAR": "')'",
    "LSQB": "'['",
    "RSQB": "']'",
    "LBRACE": "'{'",
    "RBRACE": "'}'",
    "SEMICOLON": "';'",
    "COMMA": "','",
    "LEN": "'len'",
    "MINUS": "'-'",
    "PLUS": "'+'",
    "STAR": "'*'",
    "SLASH": "'/'",
    "CALL": "'call'",
    "$END": "end of input",
}


def humanize_expected_terminals(terminals: Iterable[str]) -> list[str]:
    values = {
        _FRIENDLY_TERMINALS.get(token_name, token_name.lower().replace("_", " "))
        for token_name in terminals
    }
    return sorted(values)


def render_expected_tokens(terminals: Iterable[str]) -> str:
    expected = humanize_expected_terminals(terminals)
    if not expected:
        return "expected another valid SelVeri token"
    if len(expected) == 1:
        return f"expected {expected[0]}"
    if len(expected) == 2:
        return f"expected {expected[0]} or {expected[1]}"
    return f"expected one of: {', '.join(expected[:-1])}, or {expected[-1]}"


def format_found_token(token: object | None) -> str:
    if token is None:
        return "end of input"
    token_type = getattr(token, "type", "")
    if token_type == "$END":
        return "end of input"
    return _display_value(str(getattr(token, "value", "")))
