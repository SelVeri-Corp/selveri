from __future__ import annotations

import argparse
import ast
import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

from errors import IRRuntimeError, IRParseError
from compiler import IRInstr

@dataclass(frozen=True)
class DeclType:
    kind: str                  # "INT" | "FLOAT" | "LIST"
    elem_kind: Optional[str]   # for LIST: "INT" | "FLOAT"
    size: Optional[int]        # for LIST: fixed size for DECL, None for LDECL


@dataclass
class ExecutionResult:
    state: Dict[str, Any]
    types: Dict[str, DeclType]
    stack: List[Any]
    pc: int
    steps: int
    halted: bool


# -----------------------
# IR Parser
# -----------------------
# parse label: OP [args]
_LABEL_RE = re.compile(r"^\s*(\d+)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*(.*?)\s*$")
# parse integer
_INT_RE = re.compile(r"^[+-]?\d+$")
# parse float
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\d*\.\d+)$")
# parse list type List[INT|FLOAT[, size]]
_LIST_TEXT_RE = re.compile(r"^LIST\s*\[\s*(INT|FLOAT)\s*(?:,\s*([+-]?\d+)\s*)?\]$", re.IGNORECASE)

# this function splits the arguments into a list of strings by commas
# however as list literals also use commas, we implement depth tracking to handle nested lists correctly
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

# parse scalar token: INT | FLOAT
def _parse_scalar_token(token: str) -> Any:
    t = token.strip()
    if _INT_RE.fullmatch(t):
        return int(t)
    if _FLOAT_RE.fullmatch(t):
        return float(t)
    return t

# parse label line: LABEL OP [args]
def _parse_label_line(line: str) -> IRInstr:
    m = _LABEL_RE.match(line)
    if not m:
        raise IRParseError(f"Invalid IR line: {line}")

    label, op, rest = int(m.group(1)), m.group(2), m.group(3).strip()
    
    # handle arguments
    if not rest:
        args = ()
    elif op in {"DECL", "LDECL"}:
        args = [p.strip() for p in _split_top_level_commas(rest)]
        if len(args) != 2:
            raise IRParseError(f"Invalid {op} arguments: {line}")
    elif op == "VERI":
        args = (rest,)
    else:
        args = [_parse_scalar_token(p) for p in _split_top_level_commas(rest)]

    return IRInstr(label, op, tuple(args))


def parse_ir_text(text: str) -> List[IRInstr]:
    code: List[IRInstr] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        code.append(_parse_label_line(line))
    return code

# -----------------------
# Helpers
# -----------------------

def _type_from_object(obj: Any) -> DeclType:
    if isinstance(obj, DeclType):
        return obj

    # Strings from textual IR or repr strings
    if isinstance(obj, str):
        s = obj.strip()

        if s == "INT":
            return DeclType("INT", None, None)

        if s == "FLOAT":
            return DeclType("FLOAT", None, None)

        m = _LIST_TEXT_RE.match(s)
        if m:
            elem = m.group(1).upper()
            size = m.group(2)
            return DeclType("LIST", elem, None if size is None else int(size))

        # repr-like dataclass string, for example:
        # TypeList(elem=TypeInt(), size=IntLit(value=6))
        # TypeList(elem_type=TypeFloat(), size=IntLit(value=3))
        if s.startswith("TypeList"):
            elem_kind = "INT" if "TypeInt" in s else "FLOAT" if "TypeFloat" in s else None
            size_match = re.search(r"IntLit\s*\(\s*value\s*=\s*([+-]?\d+)\s*\)", s)
            size = int(size_match.group(1)) if size_match else None
            if elem_kind is None:
                raise IRParseError(f"Could not parse list element type from: {s}")
            return DeclType("LIST", elem_kind, size)

        raise IRParseError(f"Unknown declaration type: {obj}")

    # Objects from parser/compiler dataclasses
    cls_name = obj.__class__.__name__

    if cls_name == "TypeInt":
        return DeclType("INT", None, None)

    if cls_name == "TypeFloat":
        return DeclType("FLOAT", None, None)

    if cls_name == "TypeList":
        elem_obj = None
        if hasattr(obj, "elem"):
            elem_obj = getattr(obj, "elem")
        elif hasattr(obj, "elem_type"):
            elem_obj = getattr(obj, "elem_type")

        if elem_obj is None:
            raise IRParseError(f"Could not read list element type from: {obj}")

        elem_type = _type_from_object(elem_obj)
        if elem_type.kind != "INT" and elem_type.kind != "FLOAT":
            raise IRParseError(f"Only flat numeric lists are supported at runtime, got: {obj}")

        size_obj = getattr(obj, "size", None)
        size: Optional[int] = None
        if size_obj is not None:
            if isinstance(size_obj, int):
                size = size_obj
            elif hasattr(size_obj, "value"):
                size = int(getattr(size_obj, "value"))
            elif isinstance(size_obj, str) and _INT_RE.fullmatch(size_obj.strip()):
                size = int(size_obj.strip())

        return DeclType("LIST", elem_type.kind, size)

    raise IRParseError(f"Unknown declaration type object: {obj!r}")

# type casting
def _coerce_value(value: Any, decl_type: DeclType) -> Any:
    if decl_type.kind == "INT":
        return int(value)

    if decl_type.kind == "FLOAT":
        return float(value)

    if decl_type.kind == "LIST":
        if not isinstance(value, list):
            raise IRRuntimeError("Expected a list value.")
        if decl_type.size is not None and len(value) != decl_type.size:
            raise IRRuntimeError("List length mismatch.")
        elem_decl = DeclType(decl_type.elem_kind or "INT", None, None)
        return [_coerce_value(v, elem_decl) for v in value]

    raise IRRuntimeError(f"Unsupported type: {decl_type}")


# generic zero check
def _is_zero(v: Any) -> bool:
    return v == 0 or v == 0.0 or v is False

# converting to boolean
def _truthy(v: Any) -> bool:
    if isinstance(v, list):
        return len(v) != 0
    return not _is_zero(v)


# -----------------------
# Interpreter
# -----------------------
class SelVerIRInterpreter:
    """
    Concrete line-by-line interpreter for SelVerIR.
    """

    def __init__(
        self,
        verifier: Optional[Any] = None,
        max_steps: int = 1_000_000,
    ) -> None:
        self.verifier = verifier
        self.max_steps = max_steps

        self.code: List[IRInstr] = []
        self.label_to_index: Dict[int, int] = {}

        self.state: Dict[str, Any] = {}
        self.types: Dict[str, DeclType] = {}
        self.stack: List[Union[int, float]] = []

        self.pc: int = 0
        self.steps: int = 0

    # ---------- loading ----------
    def load(self, program: Iterable[IRInstr]) -> None:
        self.code = list(program)
        self.label_to_index = {instr.label: idx for idx, instr in enumerate(self.code)}
        self.reset_runtime() # reset the runtime state

    def reset_runtime(self) -> None:
        self.state = {} # clear the state
        self.types = {} # clear the types
        self.stack = [] # clear the stack
        self.pc = 0 # reset the program counter
        self.steps = 0 # reset the steps

    # ---------- stack ----------
    def _push(self, value: Any) -> None:
        self.stack.append(value)

    def _pop(self) -> Any:
        if not self.stack:
            raise IRRuntimeError("Stack underflow.")
        return self.stack.pop()

    # ---------- lookup ----------
    def _require_declared(self, name: str) -> None:
        if name not in self.types:
            raise IRRuntimeError(f"Undeclared variable: {name}")

    def _get_decl_type(self, name: str) -> DeclType:
        self._require_declared(name)
        return self.types[name]

    def _jump_to_label(self, label: int) -> None:
        if label not in self.label_to_index:
            raise IRRuntimeError(f"Unknown jump target label: {label}")
        self.pc = self.label_to_index[label]

    # ---------- execution ----------
    def run(
        self,
        program: Iterable[IRInstr],
        initial_state: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        self.load(program) # load the program into the interpreter

        if initial_state:
            for k, v in initial_state.items():
                self.state[k] = copy.deepcopy(v)

        while 0 <= self.pc < len(self.code):
            if self.steps >= self.max_steps:
                raise IRRuntimeError("Maximum execution step limit reached.")

            instr = self.code[self.pc]
            self._step(instr)
            self.steps += 1

        return ExecutionResult(
            state=copy.deepcopy(self.state),
            types=copy.deepcopy(self.types),
            stack=copy.deepcopy(self.stack),
            pc=self.pc,
            steps=self.steps,
            halted=True,
        )

    def _step(self, instr: IRInstr) -> None:
        op = instr.op

        if op == "DECL":
            self._exec_decl(instr.args)
            self.pc += 1
            return

        if op == "LDECL":
            self._exec_ldecl(instr.args)
            self.pc += 1
            return

        if op == "PUSH":
            self._exec_push(instr.args)
            self.pc += 1
            return

        if op == "STORE":
            self._exec_store(instr.args)
            self.pc += 1
            return

        if op == "LLOAD":
            self._exec_lload(instr.args)
            self.pc += 1
            return

        if op == "LSTORE":
            self._exec_lstore(instr.args)
            self.pc += 1
            return

        if op == "LEN":
            self._exec_len(instr.args)
            self.pc += 1
            return

        if op == "ADD":
            v1 = self._pop()
            v2 = self._pop()
            self._push(v1 + v2)
            self.pc += 1
            return

        if op == "SUB":
            v1 = self._pop()
            v2 = self._pop()
            self._push(v1 - v2)
            self.pc += 1
            return

        if op == "MUL":
            v1 = self._pop()
            v2 = self._pop()
            self._push(v1 * v2)
            self.pc += 1
            return

        if op == "iDIV":
            v1 = self._pop()
            v2 = self._pop()
            if int(v2) == 0:
                raise IRRuntimeError("Integer division by zero.")
            self._push(int(v1) // int(v2))
            self.pc += 1
            return

        if op == "fDIV":
            v1 = self._pop()
            v2 = self._pop()
            if float(v2) == 0.0:
                raise IRRuntimeError("Float division by zero.")
            self._push(float(v1) / float(v2))
            self.pc += 1
            return

        if op == "EQ":
            v1 = self._pop()
            v2 = self._pop()
            self._push(1 if v1 == v2 else 0)
            self.pc += 1
            return

        if op == "LT":
            v1 = self._pop()
            v2 = self._pop()
            self._push(1 if v1 < v2 else 0)
            self.pc += 1
            return

        if op == "LE":
            v1 = self._pop()
            v2 = self._pop()
            self._push(1 if v1 <= v2 else 0)
            self.pc += 1
            return

        if op == "GT":
            v1 = self._pop()
            v2 = self._pop()
            self._push(1 if v1 > v2 else 0)
            self.pc += 1
            return

        if op == "GE":
            v1 = self._pop()
            v2 = self._pop()
            self._push(1 if v1 >= v2 else 0)
            self.pc += 1
            return

        if op == "NEG":
            b = self._pop()
            self._push(0 if _truthy(b) else 1)
            self.pc += 1
            return

        if op == "AND":
            b1 = self._pop()
            b2 = self._pop()
            self._push(1 if (_truthy(b1) and _truthy(b2)) else 0)
            self.pc += 1
            return

        if op == "OR":
            b1 = self._pop()
            b2 = self._pop()
            self._push(1 if (_truthy(b1) or _truthy(b2)) else 0)
            self.pc += 1
            return

        if op == "XOR":
            b1 = self._pop()
            b2 = self._pop()
            self._push(1 if (_truthy(b1) ^ _truthy(b2)) else 0)
            self.pc += 1
            return

        if op == "JZ":
            if len(instr.args) != 1:
                raise IRRuntimeError("JZ expects one target label.")
            cond = self._pop()
            target = int(instr.args[0])
            if _is_zero(cond):
                self._jump_to_label(target)
            else:
                self.pc += 1
            return

        if op == "GOTO":
            if len(instr.args) != 1:
                raise IRRuntimeError("GOTO expects one target label.")
            target = int(instr.args[0])
            self._jump_to_label(target)
            return

        if op == "VERI":
            # spec = instr.args[0] if instr.args else None
            # if self.verifier is not None:
            #     self.verifier(spec, copy.deepcopy(self.state))
            self.pc += 1
            return

        if op == "NOOP":
            self.pc += 1
            return

        raise IRRuntimeError(f"Unknown instruction: {instr.op}")

    # ---------- instruction implementations ----------
    def _exec_decl(self, args: Tuple[Any, ...]) -> None:
        if len(args) != 2:
            raise IRRuntimeError("DECL expects two arguments.")
        type_obj, name_obj = args
        name = str(name_obj).strip()

        if name in self.types:
            raise IRRuntimeError(f"{name} has already been declared.") 

        decl_type = _type_from_object(type_obj)
        self.types[name] = decl_type

        # default value initialization
        if decl_type.kind == "INT":
            self.state[name] = 0
            return

        if decl_type.kind == "FLOAT":
            self.state[name] = 0.0
            return

        if decl_type.kind == "LIST":
            if decl_type.size is None:
                raise IRRuntimeError("Static list declaration requires a size.")
            zero = 0 if decl_type.elem_kind == "INT" else 0.0
            self.state[name] = [zero for _ in range(decl_type.size)]
            return

        raise IRRuntimeError(f"Unsupported DECL type: {decl_type}")

    def _exec_ldecl(self, args: Tuple[Any, ...]) -> None:
        if len(args) != 2:
            raise IRRuntimeError("LDECL expects two arguments.")
        type_obj, name_obj = args
        name = str(name_obj).strip()

        if name in self.types:
            raise IRRuntimeError(f"{name} has already been declared.")

        decl_type = _type_from_object(type_obj)
        if decl_type.kind != "LIST":
            raise IRRuntimeError("LDECL requires a list type.")

        # size is determined at runtime using the top of the stack
        size = int(self._pop())
        if size < 0:
            raise IRRuntimeError("List size cannot be negative.")

        runtime_type = DeclType("LIST", decl_type.elem_kind, size)
        self.types[name] = runtime_type

        zero = 0 if decl_type.elem_kind == "INT" else 0.0
        self.state[name] = [zero for _ in range(size)]

    def _exec_push(self, args: Tuple[Any, ...]) -> None:
        if len(args) != 1:
            raise IRRuntimeError("PUSH expects one argument.")

        arg = args[0]

        # literal values
        if isinstance(arg, (int, float)):
            self._push(arg)
            return

        # loading from a declared variable
        name = str(arg).strip()
        if name in self.types:
            value = self.state[name]
            if isinstance(value, list):
                # push x[0] as top element, matching STORE order
                for item in reversed(value):
                    self._push(item)
            else:
                self._push(value)
            return

        raise IRRuntimeError(f"Cannot PUSH unknown identifier or immediate: {arg}")

    def _exec_store(self, args: Tuple[Any, ...]) -> None:
        if len(args) != 1:
            raise IRRuntimeError("STORE expects one target.")
        name = str(args[0]).strip()
        decl_type = self._get_decl_type(name)

        if decl_type.kind in {"INT", "FLOAT"}:
            value = self._pop()
            self.state[name] = _coerce_value(value, decl_type) # type casting
            return

        if decl_type.kind == "LIST":
            if decl_type.size is None:
                raise IRRuntimeError("Cannot STORE whole list without a known list size.")
            items = [self._pop() for _ in range(decl_type.size)] # pop the items from the stack
            list_value = _coerce_value(items, decl_type) # type casting
            self.state[name] = list_value # store the list value in the state
            return

        raise IRRuntimeError(f"Unsupported STORE target type: {decl_type}")

    def _exec_lload(self, args: Tuple[Any, ...]) -> None:
        if len(args) != 1:
            raise IRRuntimeError("LLOAD expects one target list.")
        name = str(args[0]).strip() # get the name of the list
        decl_type = self._get_decl_type(name)
        if decl_type.kind != "LIST":
            raise IRRuntimeError(f"LLOAD target is not a list: {name}")

        idx = int(self._pop()) # index from the stack
        arr = self.state[name]
        if idx < 0 or idx >= len(arr):
            raise IRRuntimeError(f"List index out of bounds: {name}[{idx}]")
        self._push(arr[idx])

    def _exec_lstore(self, args: Tuple[Any, ...]) -> None:
        if len(args) != 1:
            raise IRRuntimeError("LSTORE expects one target list.")
        name = str(args[0]).strip()
        decl_type = self._get_decl_type(name)
        if decl_type.kind != "LIST":
            raise IRRuntimeError(f"LSTORE target is not a list: {name}")

        # index value from the stack
        idx = int(self._pop())
        value = self._pop()

        arr = self.state[name]
        if idx < 0 or idx >= len(arr):
            raise IRRuntimeError(f"List index out of bounds: {name}[{idx}]")

        elem_type = DeclType(decl_type.elem_kind or "INT", None, None)
        arr[idx] = _coerce_value(value, elem_type)

    def _exec_len(self, args: Tuple[Any, ...]) -> None:
        if len(args) != 1:
            raise IRRuntimeError("LEN expects one argument.")
        name = str(args[0]).strip()
        decl_type = self._get_decl_type(name)

        if decl_type.kind in {"INT", "FLOAT"}:
            self._push(0) # TODO: decide if this should throw an error or not
            return

        if decl_type.kind == "LIST":
            self._push(len(self.state[name]))
            return

        raise IRRuntimeError(f"Unsupported LEN target: {name}")


# -----------------------
# Convenience API
# -----------------------
def interpret_ir_text(
    text: str,
    initial_state: Optional[Dict[str, Any]] = None,
    verifier: Optional[Callable[[Any, Dict[str, Any]], None]] = None,
    max_steps: int = 1_000_000,
) -> ExecutionResult:
    program = parse_ir_text(text)
    return interpret_ir_code(program, initial_state=initial_state, verifier=verifier, max_steps=max_steps)

def interpret_ir_code(
    code: Iterable[IRInstr],
    initial_state: Optional[Dict[str, Any]] = None,
    verifier: Optional[Callable[[Any, Dict[str, Any]], None]] = None,
    max_steps: int = 1_000_000,
) -> ExecutionResult:
    return SelVerIRInterpreter(verifier=verifier, max_steps=max_steps).run(
        program=code,
        initial_state=initial_state,
    )

# -----------------------
# CLI for flexibility
# -----------------------
def _parse_initial_state(text: Optional[str]) -> Optional[Dict[str, Any]]:
    if text is None:
        return None
    try:
        value = ast.literal_eval(text)
    except Exception as exc:
        raise IRParseError(f"Could not parse --state literal: {exc}") from exc
    if not isinstance(value, dict):
        raise IRParseError("--state must evaluate to a dict.")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Interpret SelVerIR concretely.")
    parser.add_argument("input", type=Path, help="Path to the .svir file")
    parser.add_argument(
        "--state",
        type=str,
        default=None,
        help="Optional initial state as a Python dict literal, e.g. '{\"x\": 3}'",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=1_000_000,
        help="Maximum number of executed instructions before aborting",
    )
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        text = f.read()

    initial_state = _parse_initial_state(args.state)
    result = interpret_ir_text(
        text,
        initial_state=initial_state,
        verifier=None,
        max_steps=args.max_steps,
    )

    print("Final state:")
    print(result.state)
    print("\nFinal stack:")
    print(result.stack)
    print(f"\nSteps: {result.steps}")


if __name__ == "__main__":
    main()