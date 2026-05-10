"""
Preprocessor takes high level source code and extracts raw specs. Replaces spec annotations with placeholder tokens to keep the high level parser independent from spec syntax.
"""

from typing import Dict, List, Tuple, Optional
import re

from .errors import PreprocessorError
from .specs import RawSpec, SourceLocation, SourceSpan, RawSpecKind


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

def _scan_spec_block(
    src: str,
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
            depth += 1
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

    raise PreprocessorError("Unterminated specification annotation.")

def extract_raw_specs(src: str) -> Tuple[str, Dict[str, RawSpec]]:
    # Keep the high-level parser independent from spec syntax by replacing
    # annotation bodies with opaque placeholders before the grammar runs.
    rewritten: List[str] = []
    raw_specs: Dict[str, RawSpec] = {}
    index = 0
    line = 1
    column = 1
    next_spec_id = 0

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
                index,
                start_line,
                start_column,
            )
            try:
                kind, spec_name, formula_text = classify_raw_spec_body(raw_text)
            except ValueError as exc:
                raise PreprocessorError(str(exc)) from None
            
            identifier = ""
            if spec_name:
                identifier = spec_name
            else:
                identifier = next_spec_id
                next_spec_id += 1


            if kind == RawSpecKind.SPEC_START:
                placeholder = f"_SELVERI_SPEC_START_{identifier}"
            elif kind == RawSpecKind.SPEC_END:
                placeholder = f"_SELVERI_SPEC_END_{identifier}"
            else:
                placeholder = f"_SELVERI_SPEC_{identifier}"

            raw_specs[placeholder] = RawSpec(
                spec_id=identifier,
                formula_text=formula_text,
                location=SourceSpan(
                    start=SourceLocation(line=start_line, column=start_column),
                    end=SourceLocation(line=end_line, column=end_column),
                ),
                kind=kind
            )
            rewritten.append(placeholder)
            index = close_index + 1
            continue

        rewritten.append(ch)
        index += 1
        line, column = _advance_position(ch, line, column)

    return "".join(rewritten), raw_specs

_NAMED_SPEC_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*:=\s*(.*)$", re.DOTALL)
_SPEC_START_RE = re.compile(r"^start\s+([A-Za-z][A-Za-z0-9_]*)\s*$")
_SPEC_END_RE = re.compile(r"^end\s+([A-Za-z][A-Za-z0-9_]*)\s*$")

def classify_raw_spec_body(text: str) -> Tuple[RawSpecKind, Optional[str], Optional[str]]:
    """
    Parse a `{ ... }` body into kind, optional logical name, and formula text.

    Returns ``formula_text`` suitable for ``parse_spec`` for SPEC kinds;
    ``None`` for domain markers.
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("Empty specification annotation.")

    m_dom_s = _SPEC_START_RE.match(stripped)
    if m_dom_s: # domain start
        return RawSpecKind.DOMAIN_START, m_dom_s.group(1), None

    m_dom_e = _SPEC_END_RE.match(stripped)
    if m_dom_e: # domain end
        return RawSpecKind.DOMAIN_END, m_dom_e.group(1), None

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