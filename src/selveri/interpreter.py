from __future__ import annotations

import argparse
import ast
import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from .errors import IRRuntimeError, IRParseError
from .compiler import IRInstr
from .runtime import DeclType, RuntimeScope, _UNSET
from .verifier import RuntimeConfiguration, VerificationEngine


@dataclass
class ExecutionResult:
    state: Dict[str, Any]
    types: Dict[str, DeclType]
    stack: List[Any]
    pc: int
    steps: int
    halted: bool


@dataclass(frozen=True)
class CallFrame:
    return_pc: int
    scopes: Tuple[RuntimeScope, ...]
    function_name: Optional[str] = None
    return_type: Optional[DeclType] = None


@dataclass(frozen=True)
class FunctionEntry:
    name: str
    pc: int
    return_type: Optional[DeclType]


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

def _split_top_level_commas(s: str) -> List[str]:
    """
    Splits the arguments into a list of strings by commas.
    However as list literals also use commas, nested depth tracking is implemented to handle nested lists correctly.
    """
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
    if len(t) >= 2 and t[0] == t[-1] and t[0] in {"'", '"'}:
        return ast.literal_eval(t)
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
        args = _split_top_level_commas(rest)
        if len(args) != 2:
            raise IRParseError(f"Invalid VERI arguments: {line}")
        args = (_parse_scalar_token(args[0]), _parse_scalar_token(args[1]))
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

    if cls_name in {"TypeList", "TypeDynamicList", "TypeListParam"}:
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

        size: Optional[int] = None
        shape_obj = getattr(obj, "shape", None)
        if shape_obj:
            first_dim = shape_obj[0]
            if isinstance(first_dim, int):
                size = first_dim
            elif hasattr(first_dim, "value"):
                size = int(getattr(first_dim, "value"))
            elif isinstance(first_dim, str) and _INT_RE.fullmatch(first_dim.strip()):
                size = int(first_dim.strip())
        else:
            size_obj = getattr(obj, "size", None)
            if size_obj is not None:
                if isinstance(size_obj, int):
                    size = size_obj
                elif hasattr(size_obj, "value"):
                    size = int(getattr(size_obj, "value"))
                elif isinstance(size_obj, str) and _INT_RE.fullmatch(size_obj.strip()):
                    size = int(size_obj.strip())

        return DeclType("LIST", elem_type.kind, size)

    raise IRParseError(f"Unknown declaration type object: {obj!r}")


def _type_from_funcenv_arg(obj: Any) -> DeclType:
    if isinstance(obj, str):
        s = obj.strip()

        if s == "INT":
            return DeclType("INT", None, None)

        if s == "FLOAT":
            return DeclType("FLOAT", None, None)

        if s.upper().startswith("LIST"):
            elem_match = re.search(r"LIST\s*\[\s*(INT|FLOAT)\b", s, re.IGNORECASE)
            if elem_match:
                return DeclType("LIST", elem_match.group(1).upper(), None)

        raise IRParseError(f"Unknown FUNCENV return type: {obj}")

    decl_type = _type_from_object(obj)
    if decl_type.kind == "LIST":
        return DeclType("LIST", decl_type.elem_kind, None)
    return decl_type


def _funcenv_metadata(args: Tuple[Any, ...]) -> Tuple[Optional[str], Optional[DeclType]]:
    if len(args) == 2:
        name = str(args[0]).strip()
        if not name:
            raise IRParseError("FUNCENV function name cannot be empty.")
        return name, _type_from_funcenv_arg(args[1])

    raise IRParseError("FUNCENV expects a function name and a return type.")

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
        verifier: Optional[VerificationEngine] = None,
        max_steps: int = 1_000_000,
    ) -> None:
        self.verifier = verifier
        self.max_steps = max_steps

        self.code: List[IRInstr] = []
        self.label_to_index: Dict[int, int] = {}
        self.functions: Dict[str, FunctionEntry] = {}

        self.state: Dict[str, Any] = {}
        self.types: Dict[str, DeclType] = {}
        self.stack: List[Union[int, float]] = []
        self.scopes: List[RuntimeScope] = []
        self.call_stack: List[CallFrame] = []

        self.pc: int = 0
        self.steps: int = 0

    # ---------- loading ----------
    def load(self, program: Iterable[IRInstr]) -> None:
        self.code = list(program)
        self.label_to_index = {instr.label: idx for idx, instr in enumerate(self.code)}
        self.functions = {}
        for instr in self.code:
            if instr.op != "FUNCENV":
                continue
            name, return_type = _funcenv_metadata(instr.args)
            if name is None:
                continue
            if name in self.functions:
                raise IRParseError(f"Duplicate function environment: {name}")
            self.functions[name] = FunctionEntry(name=name, pc=instr.label, return_type=return_type)
        self.reset_runtime() # reset the runtime state
        self._prepare_verifier()

    def reset_runtime(self) -> None:
        self.stack = [] # clear the stack
        self.scopes = [self._fresh_scope()]
        self.call_stack = []
        self.pc = 0 # reset the program counter
        self.steps = 0 # reset the steps
        self._refresh_public_views()

    # ---------- stack ----------
    def _push(self, value: Any) -> None:
        self.stack.append(value)

    def _pop(self) -> Any:
        if not self.stack:
            raise IRRuntimeError("Stack underflow.")
        return self.stack.pop()

    def _pop_binary_operands(self) -> Tuple[Any, Any]:
        right = self._pop()
        left = self._pop()
        return left, right

    # ---------- lookup ----------
    def _fresh_scope(self) -> RuntimeScope:
        return RuntimeScope(values={"retvar": _UNSET}, types={"retvar": None})

    def _refresh_public_views(self) -> None:
        visible_state: Dict[str, Any] = {}
        visible_types: Dict[str, DeclType] = {}

        for scope in self.scopes:
            for name, value in scope.values.items():
                if value is not _UNSET:
                    visible_state[name] = value
            for name, decl_type in scope.types.items():
                if decl_type is not None:
                    visible_types[name] = decl_type

        self.state = visible_state
        self.types = visible_types

    def _find_scope_with_type(self, name: str) -> Optional[RuntimeScope]:
        for scope in reversed(self.scopes):
            if name in scope.types:
                return scope
        return None

    def _find_scope_with_value(self, name: str) -> Optional[RuntimeScope]:
        for scope in reversed(self.scopes):
            if name in scope.values:
                return scope
        return None

    def _require_declared(self, name: str) -> None:
        if self._find_scope_with_type(name) is None:
            raise IRRuntimeError(f"Undeclared variable: {name}")

    def _get_decl_type(self, name: str) -> DeclType:
        self._require_declared(name)
        scope = self._find_scope_with_type(name)
        assert scope is not None
        decl_type = scope.types[name]
        if decl_type is None:
            raise IRRuntimeError(f"{name} has no runtime type.")
        return decl_type

    def _get_value(self, name: str) -> Any:
        scope = self._find_scope_with_value(name)
        if scope is None:
            raise IRRuntimeError(f"Undeclared variable: {name}")
        value = scope.values[name]
        if value is _UNSET:
            raise IRRuntimeError(f"Uninitialized variable: {name}")
        return value

    def _set_value(self, name: str, value: Any) -> None:
        scope = self._find_scope_with_type(name)
        if scope is None:
            raise IRRuntimeError(f"Undeclared variable: {name}")
        scope.values[name] = value

    def _declare(self, name: str, decl_type: DeclType, value: Any) -> None:
        scope = self.scopes[-1]
        if name in scope.types and name != "retvar":
            raise IRRuntimeError(f"{name} has already been declared.")
        scope.types[name] = decl_type
        scope.values[name] = value

    def _infer_decl_type_from_value(self, value: Any) -> DeclType:
        if isinstance(value, bool):
            return DeclType("INT", None, None)
        if isinstance(value, int):
            return DeclType("INT", None, None)
        if isinstance(value, float):
            return DeclType("FLOAT", None, None)
        if isinstance(value, list):
            elem_kind = "INT"
            if any(isinstance(item, float) for item in value):
                elem_kind = "FLOAT"
            return DeclType("LIST", elem_kind, len(value))
        raise IRRuntimeError(f"Unsupported runtime value: {value!r}")

    def _set_retvar(self, value: Any, decl_type: Optional[DeclType] = None) -> None:
        scope = self.scopes[-1]
        if decl_type is None:
            scope.values["retvar"] = copy.deepcopy(value)
            scope.types["retvar"] = self._infer_decl_type_from_value(value)
            return

        coerced_value = _coerce_value(value, decl_type)
        scope.values["retvar"] = copy.deepcopy(coerced_value)
        scope.types["retvar"] = decl_type

    def _push_list_packet(self, values: List[Any]) -> None:
        for item in values:
            self._push(item)
        self._push(len(values))

    def _pop_list_packet(self, expected_size: Optional[int] = None) -> List[Any]:
        size = int(self._pop())
        if size < 0:
            raise IRRuntimeError("List size cannot be negative.")
        if expected_size is not None and size != expected_size:
            raise IRRuntimeError("List length mismatch.")
        if len(self.stack) < size:
            raise IRRuntimeError("Stack underflow while reading list packet.")
        items = [self._pop() for _ in range(size)]
        items.reverse()
        return items

    def _jump_to_label(self, label: int) -> None:
        if label not in self.label_to_index:
            raise IRRuntimeError(f"Unknown jump target label: {label}")
        self.pc = self.label_to_index[label]

    def _snapshot_runtime_configuration(self) -> RuntimeConfiguration:
        self._refresh_public_views()
        return RuntimeConfiguration(
            scope=RuntimeScope(
                values=copy.deepcopy(self.state),
                types=copy.deepcopy(self.types),
            ),
            stack=copy.deepcopy(self.stack),
        )

    # ---------- execution ----------
    def run(
        self,
        program: Iterable[IRInstr],
    ) -> ExecutionResult:
        self.load(program) # load the program into the interpreter

        while 0 <= self.pc < len(self.code):
            if self.steps >= self.max_steps:
                raise IRRuntimeError("Maximum execution step limit reached.")

            instr = self.code[self.pc]
            self._step(instr)
            self._refresh_public_views()
            self.steps += 1

        self._finish_verifier()

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

        if op == "LOAD":
            self._exec_load(instr.args)
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
            left, right = self._pop_binary_operands()
            self._push(left + right)
            self.pc += 1
            return

        if op == "SUB":
            left, right = self._pop_binary_operands()
            self._push(left - right)
            self.pc += 1
            return

        if op == "MUL":
            left, right = self._pop_binary_operands()
            self._push(left * right)
            self.pc += 1
            return

        if op == "iDIV":
            left, right = self._pop_binary_operands()
            if int(right) == 0:
                raise IRRuntimeError("Integer division by zero.")
            self._push(int(left) // int(right))
            self.pc += 1
            return

        if op == "fDIV":
            left, right = self._pop_binary_operands()
            if float(right) == 0.0:
                raise IRRuntimeError("Float division by zero.")
            self._push(float(left) / float(right))
            self.pc += 1
            return

        if op == "EQ":
            left, right = self._pop_binary_operands()
            self._push(1 if left == right else 0)
            self.pc += 1
            return

        if op == "LT":
            left, right = self._pop_binary_operands()
            self._push(1 if left < right else 0)
            self.pc += 1
            return

        if op == "LE":
            left, right = self._pop_binary_operands()
            self._push(1 if left <= right else 0)
            self.pc += 1
            return

        if op == "GT":
            left, right = self._pop_binary_operands()
            self._push(1 if left > right else 0)
            self.pc += 1
            return

        if op == "GE":
            left, right = self._pop_binary_operands()
            self._push(1 if left >= right else 0)
            self.pc += 1
            return

        if op == "NEG":
            b = self._pop()
            self._push(0 if _truthy(b) else 1)
            self.pc += 1
            return

        if op == "AND":
            left, right = self._pop_binary_operands()
            self._push(1 if (_truthy(left) and _truthy(right)) else 0)
            self.pc += 1
            return

        if op == "OR":
            left, right = self._pop_binary_operands()
            self._push(1 if (_truthy(left) or _truthy(right)) else 0)
            self.pc += 1
            return

        if op == "XOR":
            left, right = self._pop_binary_operands()
            self._push(1 if (_truthy(left) ^ _truthy(right)) else 0)
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

        if op == "CALL":
            self._exec_call(instr.args)
            return

        if op == "CSCOPE":
            self.scopes.append(self._fresh_scope())
            self.pc += 1
            return

        if op == "PSCOPE":
            if len(self.scopes) == 1:
                raise IRRuntimeError("Cannot leave the root scope.")
            self.scopes.pop()
            self.pc += 1
            return

        if op == "FUNCENV":
            if not self.call_stack:
                raise IRRuntimeError(f"{op} requires an active call frame.")
            name, return_type = _funcenv_metadata(instr.args)
            frame = self.call_stack[-1]
            if frame.function_name is not None and name != frame.function_name:
                raise IRRuntimeError(
                    f"CALL landed in function environment '{name}', expected '{frame.function_name}'."
                )
            if frame.return_type is not None and return_type is not None and return_type != frame.return_type:
                raise IRRuntimeError(f"FUNCENV return type for '{name}' does not match the function table.")
            self.call_stack[-1] = CallFrame(
                return_pc=frame.return_pc,
                scopes=frame.scopes,
                function_name=frame.function_name,
                return_type=return_type if return_type is not None else frame.return_type,
            )
            self.scopes = [self._fresh_scope()]
            self.pc += 1
            return

        if op == "RET":
            self._exec_ret(instr.args)
            return

        if op == "VERI":
            spec_id = int(instr.args[0]) if instr.args else -1
            raw_spec = instr.args[1] if len(instr.args) > 1 else None
            if self.verifier is not None:
                self._dispatch_verifier_spec(spec_id, raw_spec)
            self.pc += 1
            return

        if op == "STEP":
            if self.verifier is not None:
                self.verifier.before_step(self._snapshot_runtime_configuration())
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

        decl_type = _type_from_object(type_obj)
        if decl_type.kind == "INT":
            self._declare(name, decl_type, 0)
            return

        if decl_type.kind == "FLOAT":
            self._declare(name, decl_type, 0.0)
            return

        if decl_type.kind == "LIST":
            if decl_type.size is None:
                raise IRRuntimeError("Static list declaration requires a size.")
            zero = 0 if decl_type.elem_kind == "INT" else 0.0
            self._declare(name, decl_type, [zero for _ in range(decl_type.size)])
            return

        raise IRRuntimeError(f"Unsupported DECL type: {decl_type}")

    def _exec_ldecl(self, args: Tuple[Any, ...]) -> None:
        if len(args) != 2:
            raise IRRuntimeError("LDECL expects two arguments.")
        type_obj, name_obj = args
        name = str(name_obj).strip()

        decl_type = _type_from_object(type_obj)
        if decl_type.kind != "LIST":
            raise IRRuntimeError("LDECL requires a list type.")

        # size is determined at runtime using the top of the stack
        size = int(self._pop())
        if size < 0:
            raise IRRuntimeError("List size cannot be negative.")

        runtime_type = DeclType("LIST", decl_type.elem_kind, size)
        zero = 0 if decl_type.elem_kind == "INT" else 0.0
        self._declare(name, runtime_type, [zero for _ in range(size)])

    def _exec_push(self, args: Tuple[Any, ...]) -> None:
        if len(args) != 1:
            raise IRRuntimeError("PUSH expects one argument.")

        arg = args[0]

        # literal values
        if isinstance(arg, (int, float)):
            self._push(arg)
            return

        raise IRRuntimeError(f"Cannot PUSH unknown immediate: {arg}")

    def _exec_load(self, args: Tuple[Any, ...]) -> None:
        if len(args) != 1:
            raise IRRuntimeError("LOAD expects one source.")
        name = str(args[0]).strip()
        value = self._get_value(name)
        if isinstance(value, list):
            self._push_list_packet(value)
            return
        self._push(value)

    def _exec_store(self, args: Tuple[Any, ...]) -> None:
        if len(args) != 1:
            raise IRRuntimeError("STORE expects one target.")
        name = str(args[0]).strip()
        decl_type = self._get_decl_type(name)

        if decl_type.kind in {"INT", "FLOAT"}:
            value = self._pop()
            self._set_value(name, _coerce_value(value, decl_type))
            return

        if decl_type.kind == "LIST":
            if decl_type.size is None:
                raise IRRuntimeError("Cannot STORE whole list without a known list size.")
            items = self._pop_list_packet(expected_size=decl_type.size)
            list_value = _coerce_value(items, decl_type)
            self._set_value(name, list_value)
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
        arr = self._get_value(name)
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

        arr = self._get_value(name)
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
            raise IRRuntimeError("LEN expects a list target.")

        if decl_type.kind == "LIST":
            len_name = f"_{name}_len_1"
            len_scope = self._find_scope_with_value(len_name)
            if len_scope is not None:
                self._push(int(self._get_value(len_name)))
                return
            self._push(len(self._get_value(name)))
            return

        raise IRRuntimeError(f"Unsupported LEN target: {name}")

    def _exec_call(self, args: Tuple[Any, ...]) -> None:
        if len(args) != 1:
            raise IRRuntimeError("CALL expects one function name or label.")
        target = args[0]
        if not isinstance(target, str) or _INT_RE.fullmatch(target.strip()):
            self.call_stack.append(CallFrame(return_pc=self.pc + 1, scopes=tuple(self.scopes)))
            self._jump_to_label(int(target))
            return

        name = target.strip()
        entry = self.functions.get(name)
        if entry is None:
            raise IRRuntimeError(f"Unknown function: {name}")
        self.call_stack.append(
            CallFrame(
                return_pc=self.pc + 1,
                scopes=tuple(self.scopes),
                function_name=name,
                return_type=entry.return_type,
            )
        )
        self._jump_to_label(entry.pc)

    def _exec_ret(self, _args: Tuple[Any, ...]) -> None:
        if not self.call_stack:
            raise IRRuntimeError("RET requires an active call frame.")
        if not self.stack:
            raise IRRuntimeError("RET requires a value on the stack.")

        frame = self.call_stack[-1]
        return_type = frame.return_type

        if return_type is None:
            raise IRRuntimeError("Internal: return type of current function is None.")

        if return_type.kind == "LIST":
            return_value = self._pop_list_packet(expected_size=return_type.size)
        else:
            return_value = self._pop()

        frame = self.call_stack.pop()
        self.scopes = list(frame.scopes)
        self._set_retvar(return_value, return_type)
        self.pc = frame.return_pc

    def _prepare_verifier(self) -> None:
        if self.verifier is None:
            return
        self.verifier.prepare_program(self.code)
        self.verifier.on_program_start()

    def _finish_verifier(self) -> None:
        if self.verifier is None:
            return
        self.verifier.on_program_end()

    def _dispatch_verifier_spec(self, spec_id: int, raw_spec: Any) -> None:
        assert self.verifier is not None
        self.verifier.handle_veri(spec_id, self._snapshot_runtime_configuration())


# -----------------------
# Convenience API
# -----------------------
def interpret_ir_text(
    text: str,
    verifier: Optional[VerificationEngine] = None,
    max_steps: int = 1_000_000,
) -> ExecutionResult:
    program = parse_ir_text(text)
    return interpret_ir_code(
        program,
        verifier=verifier,
        max_steps=max_steps,
    )

def interpret_ir_code(
    code: Iterable[IRInstr],
    verifier: Optional[VerificationEngine] = None,
    max_steps: int = 1_000_000,
) -> ExecutionResult:
    return SelVerIRInterpreter(verifier=verifier, max_steps=max_steps).run(program=code)

# -----------------------
# CLI for flexibility
# -----------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Interpret SelVerIR concretely.")
    parser.add_argument("input", type=Path, help="Path to the .svir file")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=1_000_000,
        help="Maximum number of executed instructions before aborting",
    )
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        text = f.read()

    result = interpret_ir_text(
        text,
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
