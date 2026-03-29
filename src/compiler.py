from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from parser import (
    parse_selveri,
    Program,
    Stmt, Decl, Assign, ListAssign, Pass, If, While, SpecAnnot, Return,
    TypeNode, TypeInt, TypeFloat, TypeList, TypeListParam,
    AExp, IntLit, FloatLit, ListLit, AVar, ALen, AIndex, AUnOp, ABinOp, FuncCall,
    BExp, BBool, BNot, BBinOp, BCompare, BTruthy,
    FunctionDecl,
)

from errors import CompilerError

# -----------------------
# IR instruction model
# -----------------------
@dataclass
class IRInstr:
    label: int # used for the jump indexes
    op: str # operand
    args: Tuple[Union[str, int, float], ...] = () # arguments

    def render(self) -> str:
        return f"{self.label}: {self.op} " + ", ".join(str(a) for a in self.args)


# Patch reference for jumps
# As we do not know the target address of the jump until after the code is generated, we need to patch the jump address later.
@dataclass
class _PatchRef:
    idx: int  # instruction index to patch


@dataclass(frozen=True)
class _ListInfo:
    elem_type: TypeNode # the type of the elements in the list
    shape: Tuple[int, ...] # size of each dimension List[List[Int, 2], 3] has shape (3, 2)
    strides: Tuple[int, ...] # this can be deducted from shape but kept for clarity and convenience
    # strides[i] = product of shape[j] for j > i which is used in calculating the flat index
    # as an example, x[i][j][k] has flat_index = strides[0] * i + strides[1] * j + strides[2] * k
    flat_size: int # the total number of elements in the list (shape[0] * shape[1] * ... * shape[n-1])

    @property
    def dimension(self) -> int:
        return len(self.shape)

    @property
    def top_level_len(self) -> Optional[int]:
        if not self.shape:
            return 0
        return self.shape[0]

    @property
    def is_dynamic(self) -> bool:
        return self.flat_size is None or any(dim is None for dim in self.shape)


@dataclass
class _ScopeFrame:
    parent: Optional["_ScopeFrame"] = None
    caller: Optional["_ScopeFrame"] = None
    bindings: Dict[str, Optional[TypeNode]] = field(default_factory=dict)
    lists: Dict[str, _ListInfo] = field(default_factory=dict)

    def find_binding_owner(self, name: str) -> Optional["_ScopeFrame"]:
        cur: Optional[_ScopeFrame] = self
        while cur is not None:
            if name in cur.bindings and cur.bindings[name] is not None:
                return cur
            cur = cur.parent
        return None

    def get_binding(self, name: str) -> Optional[TypeNode]:
        owner = self.find_binding_owner(name)
        if owner is None:
            return None
        return owner.bindings[name]

    def find_list_owner(self, name: str) -> Optional["_ScopeFrame"]:
        cur: Optional[_ScopeFrame] = self
        while cur is not None:
            if name in cur.lists:
                return cur
            cur = cur.parent
        return None

    def get_list(self, name: str) -> Optional[_ListInfo]:
        owner = self.find_list_owner(name)
        if owner is None:
            return None
        return owner.lists[name]


def is_empty_stmt_seq(s: Optional[List[Stmt]]) -> bool:
    if s is None:
        return True
    return all(isinstance(stmt, Pass) for stmt in s)


# -----------------------
# Compiler
# -----------------------
class SelVeriCompiler:
    """
    Compiles SelVeri AST to SelVerIR.
    - Tracks compile-time scopes as a parent-linked chain.
    - Keeps retvar in each scope and updates it after calls.
    - Emits labeled IR where label == instruction index.
    """

    def __init__(self) -> None:
        # starting scope with retvar
        self.scope = _ScopeFrame() # parent scope
        self.scope.bindings["retvar"] = None
        # functions to be compiled (declaration object, pc, scope)
        self.functions: Dict[str, Tuple[FunctionDecl, int, _ScopeFrame]] = {}
        # IR program being built
        self.code: List[IRInstr] = []

    # ---------- utilities ----------
    def pc(self) -> int:
        return len(self.code) # program counter

    def emit(self, op: str, *args: Union[str, int, float]) -> int:
        self.code.append(IRInstr(self.pc(), op, args))
        return len(self.code) - 1 # index (label) of the last emitted instruction

    def patch_jump(self, patch: _PatchRef, target_pc: int) -> None:
        instr = self.code[patch.idx]
        if instr.op not in ("JZ", "GOTO"):
            raise CompilerError(f"Internal: patching non-jump at {patch.idx}: {instr.op}")
        self.code[patch.idx] = IRInstr(instr.label, instr.op, (target_pc,))

    def mangle_list_elem(self, base: str, idx: int) -> str:
        # Stable mangling for list elements
        return f"{base}[{idx}]"

    def _create_scope(self, is_env: bool = False) -> None:
        if is_env:
            self.scope = _ScopeFrame()
        else:
            self.scope = _ScopeFrame(parent=self.scope)
        self.scope.bindings["retvar"] = None

    def _parent_scope(self) -> None:
        self.scope = self.scope.parent
        if self.scope is None:
            raise CompilerError("Internal: cannot leave the root scope.")

    def _get_binding_owner(self, name: str) -> _ScopeFrame:
        owner = self.scope.find_binding_owner(name)
        if owner is None:
            raise CompilerError(f"Undeclared variable: {name}")
        return owner

    def require_declared(self, name: str) -> None:
        self._get_declared_type(name)

    def _get_declared_type(self, name: str) -> TypeNode:
        type_node = self.scope.get_binding(name)
        if type_node is None:
            if name == "retvar":
                raise CompilerError("retvar has no type until a function call returns.")
            raise CompilerError(f"Internal: unbound variable: {name}")
        return type_node

    def _declare_local(
        self,
        name: str,
        type_node: Optional[TypeNode],
        list_info: Optional[_ListInfo] = None,
    ) -> None:
        if name == "retvar":
            raise CompilerError("retvar is reserved and cannot be declared by the user.")
        if name in self.scope.bindings:
            raise CompilerError(f"Duplicate declaration: {name}")
        self.scope.bindings[name] = type_node
        if list_info is not None:
            self.scope.lists[name] = list_info

    def _declare_lowered_list_elems(self, base: str, info: _ListInfo) -> None:
        for idx in range(info.flat_size):
            elem_name = self.mangle_list_elem(base, idx)
            if elem_name in self.scope.bindings:
                raise CompilerError(f"Internal: duplicate lowered name: {elem_name}")
            self.scope.bindings[elem_name] = info.elem_type

    def _get_list_info(self, name: str) -> _ListInfo:
        info = self.scope.get_list(name)
        if info is None:
            raise CompilerError(f"'{name}' is not a declared list.")
        return info

    def _eval_const_int(self, a: AExp) -> int:
        # evaluates the constant integer value of an AExp at compile time
        # required for list literal shape matching and flat index calculation
        if isinstance(a, IntLit):
            return a.value
        if isinstance(a, FloatLit):
            raise CompilerError("Expected compile-time integer expression, found float literal.")
        if isinstance(a, ALen):
            info = self._get_list_info(a.name)
            if info.top_level_len is None:
                raise CompilerError(f"len({a.name}) is not known at compile time.")
            return info.top_level_len
        if isinstance(a, AUnOp):
            if a.op != "-":
                raise CompilerError(f"Unsupported unary op in compile-time integer expression: {a.op}")
            return -self._eval_const_int(a.rhs)
        if isinstance(a, ABinOp):
            left = self._eval_const_int(a.left)
            right = self._eval_const_int(a.right)
            if a.op == "+":
                return left + right
            if a.op == "-":
                return left - right
            if a.op == "*":
                return left * right
            if a.op == "/":
                if right == 0:
                    raise CompilerError("Division by zero in compile-time integer expression.")
                return left // right
            raise CompilerError(f"Unsupported binary op in compile-time integer expression: {a.op}")
        raise CompilerError("Expected compile-time integer expression.")

    def _flatten_list_type(self, t: TypeList) -> _ListInfo:
        shape: List[Optional[int]] = []
        elem_t: TypeNode = t
        while isinstance(elem_t, TypeList):
            dim = self._eval_const_int(elem_t.size)
            if dim < 0:
                raise CompilerError("List size cannot be negative.")
            shape.append(dim)
            elem_t = elem_t.elem

        if not isinstance(elem_t, (TypeInt, TypeFloat)):
            raise CompilerError("Lists must flatten to numeric element types.")

        strides: List[int] = []
        running = 1 # running product of dimensions
        for dim in reversed(shape):
            assert dim is not None
            strides.append(running)
            running *= dim
        strides.reverse()
        return _ListInfo(
            elem_type=elem_t,
            shape=tuple(shape),
            strides=tuple(strides),
            flat_size=running,
        )

    def _flatten_param_list_type(self, t: TypeListParam) -> _ListInfo:
        if isinstance(t.elem, TypeList):
            inner = self._flatten_list_type(t.elem)
            return _ListInfo(
                elem_type=inner.elem_type,
                shape=(None, *inner.shape),
                strides=(inner.flat_size or 1, *inner.strides),
                flat_size=None,
            )
        if isinstance(t.elem, (TypeInt, TypeFloat)):
            return _ListInfo(elem_type=t.elem, shape=(None,), strides=(1,), flat_size=None)
        raise CompilerError("Dynamic list parameters must ultimately contain numeric elements.")

    def _flat_list_decl_type(self, info: _ListInfo) -> TypeList:
        if info.flat_size is None:
            raise CompilerError("Internal: cannot emit static DECL for a dynamic list.")
        return TypeList(info.elem_type, IntLit(info.flat_size))

    def _type_from_static_shape(
        self,
        elem_type: TypeNode,
        shape: Tuple[Optional[int], ...],
    ) -> TypeNode:
        if any(dim is None for dim in shape):
            raise CompilerError("Internal: cannot reconstruct a type from a dynamic shape.")
        built: TypeNode = elem_type
        for dim in reversed(shape):
            assert dim is not None
            built = TypeList(built, IntLit(dim))
        return built

    def _extract_list_access(self, a: AIndex) -> Tuple[str, List[AExp]]:
        indices: List[AExp] = []
        cur: AExp = a
        while isinstance(cur, AIndex):
            indices.append(cur.index)
            cur = cur.base
        if not isinstance(cur, AVar):
            raise CompilerError("Only direct list variables can be indexed.")
        indices.reverse()
        return cur.name, indices

    def _build_flat_index_expr(self, info: _ListInfo, indices: List[AExp]) -> AExp:
        flat_expr: Optional[AExp] = None
        for idx_exp, stride in zip(indices, info.strides):
            term: AExp = idx_exp
            if stride != 1:
                term = ABinOp("*", idx_exp, IntLit(stride))
            flat_expr = term if flat_expr is None else ABinOp("+", flat_expr, term)

        assert flat_expr is not None
        return flat_expr

    def _resolve_list_access(self, a: AIndex) -> Tuple[_ListInfo, str, List[AExp]]:
        base_name, indices = self._extract_list_access(a)
        info = self._get_list_info(base_name)
        if len(indices) > info.dimension:
            raise CompilerError(
                f"Flattened list '{base_name}' of rank {info.dimension} "
                f"cannot be indexed with {len(indices)} indices."
            )
        return info, base_name, indices

    def _get_type_list_access(self, info: _ListInfo, indices: List[AExp]) -> TypeNode:
        if len(indices) == info.dimension:
            return info.elem_type
        remaining_shape = info.shape[len(indices):]
        if any(dim is None for dim in remaining_shape):
            raise CompilerError("Cannot use a dynamically-sized sub-list as a first-class value.")
        elem_t: TypeNode = info.elem_type
        for dim in reversed(remaining_shape):
            assert dim is not None
            elem_t = TypeList(elem_t, IntLit(dim))
        return elem_t

    def _flatten_list_literal(
        self,
        literal: Union[ListLit, IntLit, FloatLit],
        expected_type: TypeNode,
    ) -> List[Union[int, float]]:
        if isinstance(expected_type, TypeList):
            if not isinstance(literal, ListLit):
                raise CompilerError("Nested list literal shape does not match declared list type.")
            expected_len = self._eval_const_int(expected_type.size)
            if len(literal.items) != expected_len:
                raise CompilerError(
                    f"List literal length mismatch: expected {expected_len}, got {len(literal.items)}."
                )
            flat: List[Union[int, float]] = []
            for item in literal.items:
                flat.extend(self._flatten_list_literal(item, expected_type.elem))
            return flat

        if isinstance(expected_type, TypeInt):
            if not isinstance(literal, IntLit):
                raise CompilerError("Int lists require integer literals in list assignments.")
            return [literal.value]

        if isinstance(expected_type, TypeFloat):
            if not isinstance(literal, (IntLit, FloatLit)):
                raise CompilerError("Float lists require numeric literals in list assignments.")
            return [literal.value]

        raise CompilerError("Unsupported list literal assignment target type.")

    def _default_value_expr(self, type_node: TypeNode) -> AExp:
        if isinstance(type_node, TypeInt):
            return IntLit(0)
        if isinstance(type_node, TypeFloat):
            return FloatLit(0.0)
        if isinstance(type_node, TypeList):
            size = self._eval_const_int(type_node.size)
            items = [self._default_value_expr(type_node.elem) for _ in range(size)]
            return ListLit(items)
        raise CompilerError("Unsupported function return type for the return rule.")

    def _type_of_aexp(self, a: AExp) -> TypeNode:
        if isinstance(a, IntLit):
            return TypeInt()
        if isinstance(a, FloatLit):
            return TypeFloat()
        if isinstance(a, ListLit):
            raise CompilerError("List literals are not first-class arithmetic expressions.")
        if isinstance(a, AVar):
            t = self._get_declared_type(a.name)
            if isinstance(t, (TypeList, TypeListParam)):
                raise CompilerError(f"Using list variable '{a.name}' as numeric is not supported.")
            return t
        if isinstance(a, ALen):
            self._get_list_info(a.name)
            return TypeInt()
        if isinstance(a, AIndex):
            info, _base_name, indices = self._resolve_list_access(a)
            return self._get_type_list_access(info, indices)
        if isinstance(a, AUnOp):
            # unary '-' keeps numeric type
            return self._type_of_aexp(a.rhs)
        if isinstance(a, ABinOp):
            lt = self._type_of_aexp(a.left)
            rt = self._type_of_aexp(a.right)
            # float dominates
            if isinstance(lt, TypeFloat) or isinstance(rt, TypeFloat):
                return TypeFloat()
            return TypeInt()
        raise CompilerError(f"Unknown AExp node: {type(a).__name__}")

    def _has_float_subterm(self, a: AExp) -> bool:
        # used for iDIV/fDIV decision
        t = self._type_of_aexp(a)
        return isinstance(t, TypeFloat)

    def _type_of_var_for_store(self, name: str) -> TypeNode:
        return self._get_declared_type(name)

    def _ct_type(self, t: TypeNode) -> str:
        if isinstance(t, TypeInt):
            return "INT"
        if isinstance(t, TypeFloat):
            return "FLOAT"
        if isinstance(t, TypeList):
            info = self._flatten_list_type(t)
            elem_ir = self._ct_type(info.elem_type)
            return f"LIST[{elem_ir},{info.flat_size}]"
        raise CompilerError(f"Unsupported type for CT: {type(t).__name__}")

    def _are_list_types_compatible(self, actual: _ListInfo, expected: _ListInfo) -> bool:
        """
        Checks if the actual list type is compatible with the expected list type.

        Example:
        List[Int, 5] is compatible with List[Int]
        List[Int, 5] is not compatible with List[Int, 6]
        List[Int, 5] is not compatible with List[Float, 5]
        """
        if actual.elem_type != expected.elem_type:
            return False
        if actual.dimension != expected.dimension:
            return False
        for actual_dim, expected_dim in zip(actual.shape, expected.shape):
            if expected_dim is None:
                continue
            if actual_dim is None:
                return False
            if actual_dim != expected_dim:
                return False
        return True

    def _list_arg_info(self, arg: AExp) -> _ListInfo:
        if isinstance(arg, AVar):
            t = self._get_declared_type(arg.name)
            if not isinstance(t, (TypeList, TypeListParam)):
                raise CompilerError(f"Argument '{arg.name}' is not a list.")
            return self._get_list_info(arg.name)
        raise CompilerError("Only whole-list variables or list literals can be passed as list arguments.")

    def _compile_list_argument(self, arg: AExp, expected: _ListInfo, dynamic_len: bool) -> None:
        if isinstance(arg, AVar):
            actual = self._list_arg_info(arg)
            if not self._are_list_types_compatible(actual, expected):
                raise CompilerError(f"List argument '{arg.name}' does not match parameter type.")
            self.emit("LOAD", arg.name)
            if dynamic_len:
                top_len = actual.top_level_len
                if top_len is None:
                    self.emit("LEN", arg.name)
                else:
                    self.emit("PUSH", top_len)
            return

        if isinstance(arg, ListLit):
            if dynamic_len:
                literal_shape = (len(arg.items), *expected.shape[1:])
                literal_type = self._type_from_static_shape(expected.elem_type, literal_shape)
                flat_values = self._flatten_list_literal(arg, literal_type)
                top_level_len = len(arg.items)
            else:
                if not expected.shape or expected.shape[0] is None:
                    raise CompilerError("Internal: static list parameter is missing its shape.")
                literal_type = self._type_from_static_shape(expected.elem_type, expected.shape)
                flat_values = self._flatten_list_literal(arg, literal_type)
                top_level_len = expected.shape[0]
            for value in reversed(flat_values):
                self.emit("PUSH", value)
            if dynamic_len:
                self.emit("PUSH", top_level_len)
            return

        raise CompilerError("Only whole-list variables or list literals can be passed as list arguments.")

    # ---------- CA: arithmetic compilation ----------
    def CA(self, a: AExp) -> None:
        if isinstance(a, IntLit):
            self.emit("PUSH", a.value)
            return
        if isinstance(a, FloatLit):
            self.emit("PUSH", a.value)
            return

        if isinstance(a, ListLit):
            raise CompilerError("List literals are not first-class arithmetic expressions.")

        if isinstance(a, AVar):
            t = self._get_declared_type(a.name)
            if isinstance(t, (TypeList, TypeListParam)):
                raise CompilerError(f"Cannot load whole list '{a.name}' as a numeric value.")
            self.emit("LOAD", a.name)
            return

        if isinstance(a, ALen):
            info = self._get_list_info(a.name)
            # if the list is dynamic, we need to emit a LEN instruction
            if info.top_level_len is None:
                self.emit("LEN", a.name)
            else:
                # if size is known, resolve at compile time
                self.emit("PUSH", info.top_level_len)
            return

        if isinstance(a, AIndex):
            info, base_name, indices = self._resolve_list_access(a)
            if len(indices) != info.dimension:
                raise CompilerError(
                    f"Cannot load whole list expression '{base_name}[..]' as a numeric value; "
                    f"provide exactly {info.dimension} indices."
                )
            flat_index = self._build_flat_index_expr(info, indices)
            self.CA(flat_index)
            self.emit("LLOAD", base_name)
            return

        if isinstance(a, AUnOp):
            if a.op != "-":
                raise CompilerError(f"Unsupported unary op in AExp: {a.op}")
            # implementing as 0 - rhs
            self.CA(a.rhs)
            self.emit("PUSH", 0)
            self.emit("SUB")
            return

        if isinstance(a, ABinOp):
            op = a.op
            if op == "+":
                self.CA(a.right)
                self.CA(a.left)
                self.emit("ADD")
                return
            if op == "-":
                self.CA(a.right)
                self.CA(a.left)
                self.emit("SUB")
                return
            if op == "*":
                self.CA(a.right)
                self.CA(a.left)
                self.emit("MUL")
                return
            if op == "/":
                # choose iDIV vs fDIV depending on float subterm
                self.CA(a.right)
                self.CA(a.left)
                use_fdiv = self._has_float_subterm(a.left) or self._has_float_subterm(a.right)
                self.emit("fDIV" if use_fdiv else "iDIV")
                return
            raise CompilerError(f"Unsupported binary op in AExp: {op}")

        raise CompilerError(f"Unsupported AExp for CA: {type(a).__name__}")

    # ---------- CB: boolean compilation ----------
    def CB(self, b: BExp) -> None:
        if isinstance(b, BBool):
            self.emit("PUSH", 1 if b.value else 0)
            return

        # CB(a) = CA(a) if a is in AExp (truthy)
        if isinstance(b, BTruthy):
            self.CA(b.aexp)
            return

        if isinstance(b, BCompare):
            # CA(a2) : CA(a1) : OP
            self.CA(b.right)
            self.CA(b.left)
            op = b.op
            if op == "=":
                self.emit("EQ")
            elif op == "<":
                self.emit("LT")
            elif op == "<=":
                self.emit("LE")
            elif op == ">":
                self.emit("GT")
            elif op == ">=":
                self.emit("GE")
            else:
                raise CompilerError(f"Unsupported comparison op: {op}")
            return

        if isinstance(b, BNot):
            self.CB(b.rhs)
            self.emit("NEG")
            return

        if isinstance(b, BBinOp):
            self.CB(b.right)
            self.CB(b.left)
            if b.op == "and":
                self.emit("AND")
            elif b.op == "or":
                self.emit("OR")
            elif b.op == "xor":
                self.emit("XOR")
            else:
                raise CompilerError(f"Unsupported boolean binop: {b.op}")
            return

        raise CompilerError(f"Unsupported BExp for CB: {type(b).__name__}")

    # ---------- C: statements compilation ----------
    def C_stmt(self, s: Stmt) -> None:
        if isinstance(s, Decl):
            self._compile_decl(s)
            return

        if isinstance(s, Assign):
            self._compile_assign(s)
            return

        if isinstance(s, ListAssign):
            self._compile_list_assign(s)
            return

        if isinstance(s, Pass):
            self.emit("NOOP")
            return

        if isinstance(s, SpecAnnot):
            self.emit("VERI", s.spec)
            return

        if isinstance(s, If):
            self._compile_if(s)
            return

        if isinstance(s, While):
            self._compile_while(s)
            return

        if isinstance(s, Return):
            self._compile_return(s)
            return
            
        if isinstance(s, FuncCall):
            self._compile_call(s)
            return
        raise CompilerError(f"Unsupported statement: {type(s).__name__}")

    def _compile_decl(self, d: Decl) -> None:
        name = d.name
        t = d.type_node

        if isinstance(t, TypeInt):
            self._declare_local(name, t)
            self.emit("DECL", "INT", name)
            return

        if isinstance(t, TypeFloat):
            self._declare_local(name, t)
            self.emit("DECL", "FLOAT", name)
            return

        if isinstance(t, TypeList):
            info = self._flatten_list_type(t)
            self._declare_local(name, t, info)
            self._declare_lowered_list_elems(name, info)
            self.emit("DECL", self._flat_list_decl_type(info), name)
            return

        raise CompilerError(f"Unsupported type in declaration: {type(t).__name__}")

    def _compile_assign(self, a: Assign) -> None:
        name = a.name
        t = self._type_of_var_for_store(name)

        if isinstance(t, TypeListParam):
            raise CompilerError(f"Whole-list assignment for dynamic list '{name}' is not supported.")

        if isinstance(t, TypeList):
            if not isinstance(a.aexp, ListLit):
                raise CompilerError(f"Whole-list assignment for '{name}' requires a list literal.")
            flat_values = self._flatten_list_literal(a.aexp, t)
            info = self._get_list_info(name)
            if info.flat_size is None or len(flat_values) != info.flat_size:
                raise CompilerError(f"Internal: flattened literal size mismatch for '{name}'.")
            for idx, value in enumerate(flat_values):
                self.emit("PUSH", value)
                self.emit("PUSH", idx)
                self.emit("LSTORE", name)
            return

        # only numeric assigns supported (AExp from parser)
        self.CA(a.aexp)

        if not isinstance(t, (TypeInt, TypeFloat)):
            raise CompilerError(f"Unsupported store target type for {name}: {type(t).__name__}")
        self.emit("STORE", name)

    def _compile_list_assign(self, la: ListAssign) -> None: # this is the sub-list assignment like lst[0] := [1, 2, 3] or lst[0][0] := 1
        elem_info, target, indices = self._resolve_list_access(la.target)
        remaining_dimension = elem_info.dimension - len(indices)
        if remaining_dimension < 0:
            # should be caught earlier in _resolve_list_access
            raise CompilerError("Too many indices for list assignment.")

        # case 1: fully indexed element assignment (scalar store)
        if remaining_dimension == 0:
            flat_index = self._build_flat_index_expr(elem_info, indices)
            self.CA(la.aexp)
            self.CA(flat_index) # TODO: if we only use constants, we can use STORE directly instead of LSTORE with mangling names

            if not isinstance(elem_info.elem_type, (TypeInt, TypeFloat)):
                raise CompilerError("Nested list elements not supported after flattening.")
            self.emit("LSTORE", target)
            return

        # case 2: partial indexing assigning a whole sub-list, e.g., x[0] = [1, 2, 3]
        if not isinstance(la.aexp, ListLit):
            raise CompilerError("List sub-assignment requires a list literal on the right-hand side.")


        sub_type = self._get_type_list_access(elem_info, indices)
        # flatten the RHS literal according to this sub-list type
        flat_values = self._flatten_list_literal(la.aexp, sub_type)
        # compute the base flat index for the first element of the sub-list
        base_flat_expr = self._build_flat_index_expr(elem_info, indices)

        # try to resolve the base index at compile time for efficiency
        const_base: Optional[int]
        try:
            const_base = self._eval_const_int(base_flat_expr)
        except CompilerError:
            const_base = None

        # emit stores for each flattened element: index = base_flat_expr + offset
        for offset, value in enumerate(flat_values):
            self.emit("PUSH", value)
            if const_base is not None:
                # fully compile-time-known index: push the concrete integer
                self.emit("PUSH", const_base + offset)
                # TODO: we can use mangling names to directly store the value without using LSTORE in this case
            else:
                # fallback: compute index expression at runtime
                if offset == 0:
                    index_expr = base_flat_expr
                else:
                    index_expr = ABinOp("+", base_flat_expr, IntLit(offset))
                self.CA(index_expr)
            self.emit("LSTORE", target)

    def _compile_block(self, stmts: List[Stmt]) -> None:
        self.emit("CSCOPE")
        self._create_scope()
        self.C_stmt_seq(stmts)
        self._parent_scope()
        self.emit("PSCOPE")

    def _compile_if(self, s: If) -> None:
        self.CB(s.cond)
        jz_patch = _PatchRef(self.emit("JZ", -1))

        self._compile_block(s.then_s)

        if is_empty_stmt_seq(s.else_s):
            noop_pc = self.emit("NOOP")
            self.patch_jump(jz_patch, noop_pc)
            return

        goto_after_else = _PatchRef(self.emit("GOTO", -1))
        else_start_pc = self.pc()
        self._compile_block(s.else_s or [])
        noop_pc = self.emit("NOOP")

        self.patch_jump(jz_patch, else_start_pc)
        self.patch_jump(goto_after_else, noop_pc)

    def _compile_while(self, s: While) -> None:
        pcb = self.pc()
        self.CB(s.cond)
        jz_patch = _PatchRef(self.emit("JZ", -1))

        self._compile_block(s.body)

        self.emit("GOTO", pcb)
        noop_pc = self.emit("NOOP")
        self.patch_jump(jz_patch, noop_pc)

    def _compile_return(self, r: Return) -> None:
        self.CA(r.value)
        self.emit("RET")

    def _compile_call(self, call: FuncCall) -> None:
        func_info = self.functions.get(call.name)
        if func_info is None:
            raise CompilerError(f"Undefined function: {call.name}")
        func_decl, _entry_pc, _scope = func_info

        if len(call.args) != len(func_decl.params):
            raise CompilerError(
                f"Function '{call.name}' expects {len(func_decl.params)} arguments, got {len(call.args)}."
            )

        for param, arg in reversed(list(zip(func_decl.params, call.args))):
            if isinstance(param.type_node, TypeListParam):
                expected = self._flatten_param_list_type(param.type_node)
                self._compile_list_argument(arg, expected, dynamic_len=True)
            elif isinstance(param.type_node, TypeList):
                expected = self._flatten_list_type(param.type_node)
                self._compile_list_argument(arg, expected, dynamic_len=False)
            else:
                self.CA(arg)

        self.emit("CALL", call.name)
        self.scope.bindings["retvar"] = func_decl.return_type # no scope changes required in compile-time

    # ---------- program ----------
    def C_stmt_seq(self, s: List[Stmt]) -> None:
        for stmt in s:
            self.C_stmt(stmt)

    def _register_functions(self, p: Program) -> None:
        for func in p.func_decls:
            if func.name in self.functions:
                raise CompilerError(f"Duplicate function declaration: {func.name}")
            self.functions[func.name] = (func, -1, None)

    def compile_program(self, p: Program) -> List[IRInstr]:
        self._register_functions(p) # order of function declarations is not important
        # any function can call any other function regardless of the order of declarations

        for func in p.func_decls:
            self._compile_function_decl(func)

        self.C_stmt_seq(p.stmt_seq)
        return self.code

    def _compile_function_decl(self, f: FunctionDecl) -> None:
        old_scope = self.scope
        new_scope = _ScopeFrame()
        self.functions[f.name] = (f, self.pc(), new_scope) # update pc and scope of the record

        self.emit("ENV")
        self.scope = new_scope
        for param in f.params:
            name = param.name
            t = param.type_node
            if name == "retvar":
                raise CompilerError("retvar is reserved and cannot be used as a parameter name.")

            if isinstance(t, TypeListParam):
                info = self._flatten_param_list_type(t)
                elem_ir = self._ct_type(info.elem_type)
                self._declare_local(name, t, info)
                self.emit("LDECL", f"LIST[{elem_ir}]", name)
            elif isinstance(t, TypeList):
                info = self._flatten_list_type(t)
                self._declare_local(name, t, info)
                self._declare_lowered_list_elems(name, info)
                self.emit("DECL", self._ct_type(t), name)
            else:
                self._declare_local(name, t)
                self.emit("DECL", self._ct_type(t), name)
            self.emit("STORE", name) # used to fetch the parameter value from the stack

        # function body must end with a return statement
        if not f.body or not isinstance(f.body[-1], Return):
            f.body.append(Return(self._default_value_expr(f.return_type)))

        self.C_stmt_seq(f.body)
        self.scope = old_scope # restore the original scope

    def compile_to_text(self, p: Program) -> str:
        code = self.compile_program(p)
        lines = [instr.render() for instr in code] # converts instruction objects into string representations
        return "\n".join(lines)


# -----------------------
# Convenience API
# -----------------------
def compile_selveri_source_to_ir_text(src: str) -> str:
    ast = parse_selveri(src)
    return SelVeriCompiler().compile_to_text(ast)


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(description="Compile SelVeri source to SelVerIR")
    arg_parser.add_argument("input", type=Path, help="Path to the .sv source file")
    arg_parser.add_argument("-o", "--output", type=Path, help="Path for the output IR file")
    args = arg_parser.parse_args()
    if args.output is None:
        args.output = args.input.with_suffix(".svir")
    with open(args.input, "r", encoding="utf-8") as f:
        src = f.read()
    ir_text = compile_selveri_source_to_ir_text(src)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(ir_text)
