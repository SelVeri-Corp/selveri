"""
Preprocessor takes high level source code and extracts raw specs. Replaces spec annotations with placeholder tokens to keep the high level parser independent from spec syntax.
"""

from typing import Dict, List, Tuple, Optional
import re

from selveri.common.diagnostics import DiagnosticCode, DiagnosticLabel, SourceFile, SourceSpan
from selveri.common.errors import PreprocessorError
from selveri.spec.models import RawSpec, RawSpecKind


def _advance_position(ch: str, line: int, column: int) -> Tuple[int, int]:
    if ch == "\n":
        return line + 1, 1
    return line, column + 1

def _consume_line_comment(src: str, start: int, line: int, column: int) -> Tuple[int, int, int]:
    index = start
    cur_line = line
    cur_column = column
    while index < len(src):
        ch = src[index]
        index += 1
        cur_line, cur_column = _advance_position(ch, cur_line, cur_column)
        if ch == "\n":
            break
    return index, cur_line, cur_column

def _consume_block_comment(src: str, start: int, line: int, column: int) -> Tuple[int, int, int]:
    index = start
    cur_line = line
    cur_column = column
    while index < len(src):
        if src.startswith("*/", index):
            cur_line, cur_column = _advance_position("*", cur_line, cur_column)
            cur_line, cur_column = _advance_position("/", cur_line, cur_column)
            return index + 2, cur_line, cur_column
        ch = src[index]
        index += 1
        cur_line, cur_column = _advance_position(ch, cur_line, cur_column)
    raise PreprocessorError("Unterminated block comment.")


def _preprocessor_error(
    *,
    code: str,
    title: str,
    message: str,
    span: SourceSpan,
    label: str,
    hint: str | None = None,
) -> PreprocessorError:
    return PreprocessorError(
        message,
        code=code,
        title=title,
        span=span,
        labels=(DiagnosticLabel(span, label, "primary"),),
        hint=hint,
    )

def _scan_spec_block(
    src: str,
    source: SourceFile,
    start_index: int,
    start_line: int,
    start_column: int,
) -> Tuple[str, int, int, int, int, int]:
    depth = 1
    body_start = start_index + 1
    index = body_start
    cur_line, cur_column = _advance_position("{", start_line, start_column)

    while index < len(src):
        if src.startswith("//", index):
            index, cur_line, cur_column = _consume_line_comment(src, index, cur_line, cur_column)
            continue
        if src.startswith("/*", index):
            index, cur_line, cur_column = _consume_block_comment(src, index, cur_line, cur_column)
            continue

        ch = src[index]
        if ch == "{":
            nested_span = SourceSpan.from_positions(source, start_pos=index, end_pos=index + 1)
            raise _preprocessor_error(
                code=DiagnosticCode.PREPROCESSOR_NESTED_SPEC,
                title="nested specification block",
                message="specification blocks cannot be nested",
                span=nested_span,
                label="nested `{` is not allowed inside a specification block",
                hint="remove the inner braces or rewrite the predicate",
            )
        elif ch == "}":
            depth -= 1
            if depth == 0:
                raw_text = src[body_start:index].lstrip().rstrip()
                close_line = cur_line
                close_column = cur_column
                after_line, after_column = _advance_position("}", cur_line, cur_column)
                return raw_text, index, close_line, close_column, after_line, after_column

        index += 1
        cur_line, cur_column = _advance_position(ch, cur_line, cur_column)

    start_span = SourceSpan.from_positions(source, start_pos=start_index, end_pos=start_index + 1)
    raise _preprocessor_error(
        code=DiagnosticCode.PREPROCESSOR_UNCLOSED_SPEC,
        title="unclosed specification block",
        message="expected closing `}`",
        span=start_span,
        label="specification block starts here",
        hint="close the specification block with `}`",
    )

def _layout_preserving_placeholder(consumed: str, placeholder: str) -> str:
    """
    Preserve the layout of the original source code by adding spaces to the placeholder.
    """
    if "\n" not in consumed:
        if len(placeholder) <= len(consumed):
            return placeholder + (" " * (len(consumed) - len(placeholder)))
        return placeholder

    tail_width = len(consumed.rsplit("\n", 1)[-1])
    return placeholder + ("\n" * consumed.count("\n")) + (" " * tail_width)


def extract_raw_specs(src: str, source_file: SourceFile | None = None) -> Tuple[str, Dict[str, RawSpec]]:
    # Keep the high-level parser independent from spec syntax by replacing
    # annotation bodies with opaque placeholders before the grammar runs.
    rewritten: List[str] = []
    raw_specs: Dict[str, RawSpec] = {}
    index = 0
    line = 1
    column = 1
    next_spec_id = 0

    source = source_file or SourceFile("", src)
    while index < len(src):
        if src.startswith("//", index):
            next_index, line, column = _consume_line_comment(src, index, line, column)
            rewritten.append(src[index:next_index])
            index = next_index
            continue

        if src.startswith("/*", index):
            next_index, line, column = _consume_block_comment(src, index, line, column)
            rewritten.append(src[index:next_index])
            index = next_index
            continue

        ch = src[index]
        if ch == "{":
            start_line = line
            start_column = column
            raw_text, close_index, end_line, end_column, line, column = _scan_spec_block(
                src,
                source,
                index,
                start_line,
                start_column,
            )
            try:
                kind, spec_name, formula_text = classify_raw_spec_body(raw_text)
            except ValueError as exc:
                span = SourceSpan(source, start_line, start_column, end_line, end_column + 1, index, close_index + 1)
                if "Empty specification" in str(exc):
                    raise _preprocessor_error(
                        code=DiagnosticCode.PREPROCESSOR_EMPTY_SPEC,
                        title="empty specification",
                        message="a specification block must contain a predicate",
                        span=span,
                        label="specification block is empty",
                        hint="write a condition such as `{ x > 0 }`",
                    ) from None
                raise PreprocessorError(str(exc), span=span) from None
            
            identifier = ""
            if spec_name:
                identifier = spec_name
            else:
                identifier = next_spec_id
                next_spec_id += 1


            if kind == RawSpecKind.SPEC_START:
                placeholder = f"@S_{identifier}"
            elif kind == RawSpecKind.SPEC_END:
                placeholder = f"@E_{identifier}"
            elif kind == RawSpecKind.SPEC_NAMED:
                placeholder = f"@N_{identifier}"
            else:
                placeholder = f"@{identifier}"
            raw_specs[placeholder] = RawSpec(
                spec_id=identifier,
                formula_text=formula_text,
                location=SourceSpan(
                    source=source,
                    start_line=start_line,
                    start_column=start_column,
                    end_line=end_line,
                    end_column=end_column + 1,
                    start_pos=index,
                    end_pos=close_index + 1,
                ),
                kind=kind
            )
            rewritten.append(_layout_preserving_placeholder(src[index : close_index + 1], placeholder))
            index = close_index + 1
            continue

        rewritten.append(ch)
        index += 1
        line, column = _advance_position(ch, line, column)

    return "".join(rewritten), raw_specs

_IDENT_RE = r"[A-Za-z\u0370-\u03FF\u1F00-\u1FFF][A-Za-z0-9_\u0370-\u03FF\u1F00-\u1FFF]*"
_NAMED_SPEC_RE = re.compile(rf"^({_IDENT_RE})\s*:=\s*(.*)$", re.DOTALL)
_SPEC_START_RE = re.compile(rf"^start\s+({_IDENT_RE})\s*$")
_SPEC_END_RE = re.compile(rf"^end\s+({_IDENT_RE})\s*$")

def classify_raw_spec_body(text: str) -> Tuple[RawSpecKind, Optional[str], Optional[str]]:
    """
    Parse a `{ ... }` body into kind, optional logical name, and formula text.

    Returns ``formula_text`` suitable for ``parse_spec`` for SPEC kinds;
    ``None`` for spec markers.
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("Empty specification annotation.")

    m_spec_s = _SPEC_START_RE.match(stripped)
    if m_spec_s: # spec start
        return RawSpecKind.SPEC_START, m_spec_s.group(1), None

    m_spec_e = _SPEC_END_RE.match(stripped)
    if m_spec_e: # spec end
        return RawSpecKind.SPEC_END, m_spec_e.group(1), None

    m_assign = _NAMED_SPEC_RE.match(stripped)
    if m_assign: # named spec
        name, formula = m_assign.group(1), m_assign.group(2).strip()
        if name in ("start", "end"):
            raise ValueError(f"Reserved specification name '{name}'; choose another identifier.")
        if not formula:
            raise ValueError(f"Named specification '{name}' has an empty formula.")
        return RawSpecKind.SPEC_NAMED, name, formula

    # spec without a name
    return RawSpecKind.SPEC, None, stripped
