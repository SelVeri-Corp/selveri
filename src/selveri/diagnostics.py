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

    def is_multiline(self) -> bool:
        return self.start_line != self.end_line

    def with_source(self, source: SourceFile) -> "SourceSpan":
        return SourceSpan(
            source=source,
            start_line=self.start_line,
            start_column=self.start_column,
            end_line=self.end_line,
            end_column=self.end_column,
            start_pos=self.start_pos,
            end_pos=self.end_pos,
        )

    def merge(self, other: "SourceSpan") -> "SourceSpan":
        if self.source is not other.source and self.source != other.source:
            raise ValueError("Cannot merge spans from different source files.")
        first = self if self.start_pos <= other.start_pos else other
        last = self if self.end_pos >= other.end_pos else other
        return SourceSpan(
            source=first.source,
            start_line=first.start_line,
            start_column=first.start_column,
            end_line=last.end_line,
            end_column=last.end_column,
            start_pos=min(self.start_pos, other.start_pos),
            end_pos=max(self.end_pos, other.end_pos),
        )

    def contains(self, other: "SourceSpan") -> bool:
        return (
            self.source == other.source
            and self.start_pos <= other.start_pos
            and self.end_pos >= other.end_pos
        )

    @classmethod
    def from_positions(
        cls,
        source: SourceFile,
        *,
        start_pos: int,
        end_pos: int | None = None,
    ) -> "SourceSpan":
        end_pos = start_pos + 1 if end_pos is None else max(end_pos, start_pos + 1)
        line = column = 1
        start_line = start_column = end_line = end_column = 1
        for index, ch in enumerate(source.text):
            if index == start_pos:
                start_line, start_column = line, column
            if index == end_pos:
                end_line, end_column = line, column
                break
            if ch == "\n":
                line, column = line + 1, 1
            else:
                column += 1
        else:
            if start_pos >= len(source.text):
                start_line, start_column = line, column
            end_line, end_column = line, column
        return cls(source, start_line, start_column, end_line, end_column, start_pos, end_pos)

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
class DiagnosticLabel:
    span: SourceSpan
    message: str | None = None
    style: str = "primary"


@dataclass(frozen=True)
class DiagnosticNote:
    message: str
    kind: str = "note"


@dataclass(frozen=True)
class DiagnosticFix:
    message: str
    replacement: str | None = None
    span: SourceSpan | None = None


@dataclass(frozen=True)
class TraceEntry:
    step: int
    span: SourceSpan | None
    description: str
    values: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Counterexample:
    values: Mapping[str, object]
    before_values: Mapping[str, object] = field(default_factory=dict)
    after_values: Mapping[str, object] = field(default_factory=dict)
    step: int | None = None
    trace: tuple[TraceEntry, ...] = ()


class DiagnosticCode:
    PARSE_ERROR = "SV-P000"
    PARSE_UNEXPECTED_TOKEN = "SV-P001"
    PARSE_MISSING_BLOCK_TERMINATOR = "SV-P002"
    PREPROCESSOR_ERROR = "SV-PP000"
    PREPROCESSOR_UNCLOSED_SPEC = "SV-PP001"
    PREPROCESSOR_EMPTY_SPEC = "SV-PP002"
    PREPROCESSOR_NESTED_SPEC = "SV-PP003"
    COMPILE_ERROR = "SV-C000"
    COMPILE_DUPLICATE_DECLARATION = "SV-C001"
    COMPILE_TYPE_MISMATCH = "SV-C002"
    COMPILE_UNKNOWN_IDENTIFIER = "SV-C003"
    COMPILE_INVALID_LIST_INDEX = "SV-C004"
    COMPILE_INVALID_RETURN_TYPE = "SV-C005"
    RUNTIME_ERROR = "SV-R000"
    RUNTIME_DIVISION_BY_ZERO = "SV-R001"
    RUNTIME_INDEX_OUT_OF_BOUNDS = "SV-R002"
    RUNTIME_INVALID_INPUT_VALUE = "SV-R003"
    RUNTIME_OBTAIN_FAILED = "SV-R004"
    IR_PARSE_ERROR = "SV-IR000"
    VERIFIER_RUNTIME_ERROR = "SV-VR000"
    VERIFICATION_ERROR = "SV-V000"
    VERIFICATION_ASSERTION_FAILED = "SV-V001"
    VERIFICATION_SOLVER_UNKNOWN = "SV-V002"
    VERIFICATION_TEMPORAL_VIOLATED = "SV-V003"
    VERIFICATION_TEMPORAL_NOT_SATISFIED = "SV-V004"
    VERIFICATION_INVALID_START_BOUND = "SV-V005"
    VERIFICATION_INVALID_END_BOUND = "SV-V006"
    VERIFICATION_INVALID_SPECIFICATION = "SV-V007"


@dataclass(frozen=True)
class Diagnostic:
    code: str | None
    severity: DiagnosticSeverity
    category: str
    title: str
    message: str
    span: SourceSpan | None
    notes: tuple[DiagnosticNote | str, ...] = field(default_factory=tuple)
    hint: str | None = None
    labels: tuple[DiagnosticLabel, ...] = field(default_factory=tuple)
    fixes: tuple[DiagnosticFix, ...] = field(default_factory=tuple)
    context: Mapping[str, object] = field(default_factory=dict)
    counterexample: Counterexample | None = None


def make_diagnostic(
    *,
    category: str,
    title: str,
    message: str,
    span: SourceSpan | None = None,
    code: str | None = None,
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
    notes: Sequence[DiagnosticNote | str] = (),
    hint: str | None = None,
    labels: Sequence[DiagnosticLabel] = (),
    fixes: Sequence[DiagnosticFix] = (),
    context: Mapping[str, object] | None = None,
    counterexample: Counterexample | Mapping[str, object] | None = None,
) -> Diagnostic:
    resolved_counterexample = (
        Counterexample(counterexample)
        if counterexample is not None and not isinstance(counterexample, Counterexample)
        else counterexample
    )
    return Diagnostic(
        code=code,
        severity=severity,
        category=category,
        title=title,
        message=message,
        span=span,
        notes=tuple(notes),
        hint=hint,
        labels=tuple(labels),
        fixes=tuple(fixes),
        context={} if context is None else dict(context),
        counterexample=resolved_counterexample,
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


def _format_pointer(span: SourceSpan, message: str | None = None) -> tuple[str, str, str | None]:
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
    if message:
        pointer += f" {message}"

    continuation = None
    if span.start_line != span.end_line:
        continuation = (
            f"span continues through line {span.end_line}, column {span.end_column}"
        )
    return display_line, pointer, continuation


def _primary_label_for(diag: Diagnostic) -> DiagnosticLabel | None:
    for label in diag.labels:
        if label.style == "primary":
            return label
    if diag.span is not None:
        return DiagnosticLabel(diag.span)
    return None


def _secondary_labels(diag: Diagnostic) -> list[DiagnosticLabel]:
    primary = _primary_label_for(diag)
    labels: list[DiagnosticLabel] = []
    for label in diag.labels:
        if primary is not None and label is primary:
            continue
        if label.style != "primary":
            labels.append(label)
    return labels


def _render_labeled_span(
    label: DiagnosticLabel,
    *,
    color: bool,
    prefix: str | None = None,
) -> list[str]:
    span = label.span
    out: list[str] = []
    if prefix:
        out.append(prefix)
    out.append(
        _style(" --> ", _ANSI_CYAN, color=color) + _style(span.location, _ANSI_BOLD, color=color)
    )
    out.append(_style("  |", _ANSI_DIM, color=color))
    first = max(1, span.start_line - 1)
    last = min(len(span.source._lines), span.start_line + 1)
    width = len(str(last))
    for line_no in range(first, last + 1):
        raw_line = span.source.get_line(line_no)
        out.append(
            _style(str(line_no).rjust(width), _ANSI_CYAN, color=color)
            + _style(" | ", _ANSI_DIM, color=color)
            + raw_line.expandtabs(4)
        )
        if line_no < span.start_line or line_no > span.end_line:
            continue
        line_span = span
        if line_no != span.start_line:
            line_span = SourceSpan(
                span.source,
                line_no,
                1,
                line_no,
                span.end_column if line_no == span.end_line else max(len(raw_line), 1) + 1,
                span.start_pos,
                span.end_pos,
            )
        _, pointer, continuation = _format_pointer(
            line_span,
            label.message if line_no == span.start_line else None,
        )
        color_code = _ANSI_RED if label.style == "primary" else _ANSI_BLUE
        out.append(
            " " * width
            + _style(" | ", _ANSI_DIM, color=color)
            + _style(pointer, color_code, _ANSI_BOLD, color=color)
        )
        if continuation is not None and line_no == span.start_line:
            out.append(
                " " * width
                + _style(" | ", _ANSI_DIM, color=color)
                + _style(continuation, _ANSI_DIM, color=color)
            )
    return out


def _note_parts(note: DiagnosticNote | str) -> tuple[str, str]:
    if isinstance(note, DiagnosticNote):
        return note.kind, note.message
    return "note", note


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value if value.startswith(("'", '"', "`")) else repr(value)
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{k}: {_format_value(v)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_value(v) for v in value) + "]"
    return str(value)


def _render_counterexample(counterexample: Counterexample, *, color: bool) -> list[str]:
    lines: list[str] = []
    if counterexample.trace:
        lines.append(_style("= ", _ANSI_BOLD, color=color) + "counterexample trace:")
        for entry in counterexample.trace:
            lines.append(f"  step {entry.step}:")
            if entry.description:
                lines.append(f"    {entry.description}")
            for name, value in entry.values.items():
                lines.append(f"    {name} = {_format_value(value)}")
        return lines
    if counterexample.before_values:
        lines.append(_style("= ", _ANSI_BOLD, color=color) + "counterexample before iteration:")
        for name, value in counterexample.before_values.items():
            lines.append(f"  {name} = {_format_value(value)}")
    if counterexample.after_values:
        lines.append(_style("= ", _ANSI_BOLD, color=color) + "counterexample after iteration:")
        for name, value in counterexample.after_values.items():
            lines.append(f"  {name} = {_format_value(value)}")
    if counterexample.values:
        heading = "counterexample"
        if counterexample.step is not None:
            heading += f" at step {counterexample.step}"
        lines.append(_style("= ", _ANSI_BOLD, color=color) + f"{heading}:")
        for name, value in counterexample.values.items():
            lines.append(f"  {name} = {_format_value(value)}")
    return lines


def render_diagnostic(diag: Diagnostic, *, color: bool = False) -> str:
    lines = [_format_header(diag, color=color)]
    primary = _primary_label_for(diag)
    if primary is not None:
        lines.extend(_render_labeled_span(primary, color=color))
    for secondary in _secondary_labels(diag):
        lines.append(_style("  |", _ANSI_DIM, color=color))
        lines.extend(
            _render_labeled_span(
                secondary,
                color=color,
                prefix=f"{secondary.style if secondary.style != 'secondary' else 'note'}:",
            )
        )
    lines.append("")
    lines.append(_style("= ", _ANSI_BOLD, color=color) + diag.message)
    for note in diag.notes:
        kind, message = _note_parts(note)
        lines.append(
            _style("= ", _ANSI_BOLD, color=color)
            + _style(f"{kind}:", _ANSI_BLUE, _ANSI_BOLD, color=color)
            + f" {message}"
        )
    if diag.counterexample is not None:
        lines.extend(_render_counterexample(diag.counterexample, color=color))
    for fix in diag.fixes:
        lines.append(
            _style("= ", _ANSI_BOLD, color=color)
            + _style("fix:", _ANSI_GREEN, _ANSI_BOLD, color=color)
            + f" {fix.message}"
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
    "REAL_LIT": "real literal",
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
