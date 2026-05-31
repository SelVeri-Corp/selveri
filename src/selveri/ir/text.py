from __future__ import annotations

import ast
import re
from typing import Any, List, Optional

from selveri.common.errors import IRParseError
from selveri.common.types import real
from selveri.ir.instr import IRInstr

_LABEL_RE = re.compile(r"^\s*(\d+)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*(.*?)\s*$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_REAL_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\d*\.\d+)$")


def _split_top_level_commas(s: str) -> List[str]:
    parts: List[str] = []
    cur: List[str] = []
    depth_par = 0
    depth_brk = 0
    depth_brc = 0
    in_str = False
    str_char = ""

    for ch in s:
        if in_str:
            cur.append(ch)
            if ch == str_char:
                in_str = False
            continue

        if ch in ("'", '"'):
            in_str = True
            str_char = ch
            cur.append(ch)
            continue

        if ch == "(":
            depth_par += 1
        elif ch == ")":
            depth_par -= 1
        elif ch == "[":
            depth_brk += 1
        elif ch == "]":
            depth_brk -= 1
        elif ch == "{":
            depth_brc += 1
        elif ch == "}":
            depth_brc -= 1
        elif ch == "," and depth_par == 0 and depth_brk == 0 and depth_brc == 0:
            part = "".join(cur).strip()
            if part:
                parts.append(part)
            cur = []
            continue

        cur.append(ch)

    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def parse_scalar_token(token: str) -> Any:
    t = token.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in {"'", '"'}:
        return ast.literal_eval(t)
    if _INT_RE.fullmatch(t):
        return int(t)
    if _REAL_RE.fullmatch(t):
        return real(t)
    return t


def parse_int_literal(token: str) -> Optional[int]:
    """Parse a token as an integer literal, or return None if it is not one."""
    parsed = parse_scalar_token(token)
    return parsed if isinstance(parsed, int) else None


def resolve_label_target(target: Any) -> Optional[int]:
    """Return a jump label if *target* is an IR label (int or int literal text)."""
    if isinstance(target, int):
        return target
    if isinstance(target, str):
        return parse_int_literal(target)
    return None


def coerce_ir_int(value: Any) -> Optional[int]:
    """Coerce IR/compiler metadata (int, IntLit, or int literal string) to int."""
    if isinstance(value, int):
        return value
    if hasattr(value, "value"):
        inner = getattr(value, "value")
        if isinstance(inner, int):
            return inner
        if isinstance(inner, str):
            return parse_int_literal(inner)
        return None
    if isinstance(value, str):
        return parse_int_literal(value)
    return None


def _parse_label_line(line: str) -> IRInstr:
    m = _LABEL_RE.match(line)
    if not m:
        raise IRParseError(f"Invalid IR line: {line}")

    label, op, rest = int(m.group(1)), m.group(2), m.group(3).strip()

    if not rest:
        args = ()
    elif op in {"DECL", "LDECL"}:
        args = [p.strip() for p in _split_top_level_commas(rest)]
        if len(args) != 2:
            raise IRParseError(f"Invalid {op} arguments: {line}")
    elif op == "VERI":
        args = _split_top_level_commas(rest)
        if len(args) != 2:
            raise IRParseError(f"Invalid VERI arguments: {line}")
        args = (parse_scalar_token(args[0]), parse_scalar_token(args[1]))
    elif op == "VERIP":
        args = _split_top_level_commas(rest)
        if len(args) != 2:
            raise IRParseError(f"Invalid VERIP arguments: {line}")
        args = (parse_scalar_token(args[0]), parse_scalar_token(args[1]))
    elif op == "OBT":
        args = _split_top_level_commas(rest)
        if len(args) != 3:
            raise IRParseError(f"Invalid OBT arguments: {line}")
        args = (args[0].strip(), parse_scalar_token(args[1]), parse_scalar_token(args[2]))
    else:
        args = [parse_scalar_token(p) for p in _split_top_level_commas(rest)]

    return IRInstr(label, op, tuple(args), None)


def parse_ir_text(text: str) -> List[IRInstr]:
    code: List[IRInstr] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        code.append(_parse_label_line(line))
    return code
