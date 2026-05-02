from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from .parser import (
    parse_selveri,
    Program,
    Stmt, Decl, Assign, ListAssign, Pass, If, While, SpecAnnot, Return,
    TypeNode, BasicType, TypeInt, TypeFloat, TypeList, TypeDynamicList,
    AExp, IntLit, FloatLit, ListLit, AVar, ALen, AIndex, AUnOp, ABinOp, ARead, FuncCall,
    BExp, BBool, BNot, BBinOp, BCompare, BTruthy,
    FunctionDecl, Write, WriteLine,
)
from .specs import RawSpec

from .errors import CompilerError

RESERVED_NAMES = ["retvar", "read", "write", "len"]

# -----------------------
# IR instruction model
# -----------------------
@dataclass
class IRInstr:
    label: int # used for the jump indexes
    op: str # operand
    args: Tuple[Union[str, int, float], ...] = () # arguments

    def render(self) -> str:
        rendered_args = ", ".join(self._render_arg(index, arg) for index, arg in enumerate(self.args))
        return f"{self.label}: {self.op}" + (f" {rendered_args}" if rendered_args else "")

    def _render_arg(self, index: int, arg: Union[str, int, float]) -> str:
        if self.op == "VERI" and index == 1 and isinstance(arg, str):
            return repr(arg)
        return str(arg)


# Patch reference for jumps
# As we do not know the target address of the jump until after the code is generated, we need to patch the jump address later.
@dataclass
class _PatchRef:
    idx: int # instruction index to patch


@dataclass(frozen=True)
class _ListInfo:
    elem_type: TypeNode # the type of the elements in the list
    dimension: int # number of dimensions
    shape: Tuple[int, ...] # size of each dimension List[List[Int, 2], 3] has shape (3, 2)

    @property
    def top_level_len(self) -> Optional[int]:
        if not self.shape:
            return 0
        return self.shape[0]

    @property
    def flat_size(self) -> Optional[int]:
        total = 1
        for dim in self.shape:
            if dim is None:
                return None
            total *= dim
        return total


@dataclass
class _ScopeFrame:
    parent: Optional["_ScopeFrame"] = None
    bindings: Dict[str, Optional[TypeNode]] = field(default_factory=dict)
    lists: Dict[str, _ListInfo] = field(default_factory=dict)

    def find_binding_owner(self, name: str) -> Optional["_ScopeFrame"]:
        cur: Optional[_ScopeFrame] = self
        while cur is not None:
            if name in cur.bindings:
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


def is_empty_stmt_seq(stmts: Optional[List[Stmt]]) -> bool:
    if stmts is None:
        return True
    return all(isinstance(stmt, Pass) for stmt in stmts)


def stmt_guarantees_return(stmt: Stmt) -> bool:
    if isinstance(stmt, Return):
        return True
    if isinstance(stmt, If):
        return (
            stmt.else_s is not None
            and stmt_seq_guarantees_return(stmt.then_s)
            and stmt_seq_guarantees_return(stmt.else_s)
        )
    return False


def stmt_seq_guarantees_return(stmts: Optional[List[Stmt]]) -> bool:
    if not stmts:
        return False
    return stmt_guarantees_return(stmts[-1])


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
        self.current_return_type: Optional[TypeNode] = None
        # functions to be compiled (declaration object, pc)
        self.functions: Dict[str, Tuple[FunctionDecl, int]] = {}
        # IR program being built
        self.code: List[IRInstr] = []
        self.raw_specs: Dict[int, RawSpec] = {}

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

    def _create_scope(self, fresh_env: bool = False) -> None:
        parent = None if fresh_env else self.scope
        self.scope = _ScopeFrame(parent=parent)
        self.scope.bindings["retvar"] = None

    def _leave_scope(self) -> None:
        if self.scope.parent is None:
            raise CompilerError("Internal: cannot leave the root scope.")
        self.scope = self.scope.parent

    def _list_len_name(self, base: str, dim: int) -> str:
        return f"_{base}_len_{dim}"

    def _get_declared_type(self, name: str) -> TypeNode:
        owner = self.scope.find_binding_owner(name)
        if owner is None:
            raise CompilerError(f"Undeclared variable: {name}")
        type_node = owner.bindings[name]
        if type_node is None:
            if name == "retvar":
                raise CompilerError("retvar has no type until a function call returns.")
            raise CompilerError(f"Internal: unbound variable: {name}")
        return type_node

    def _get_list_info(self, name: str) -> _ListInfo:
        info = self.scope.get_list(name)
        if info is not None:
            return info

        type_node = self.scope.get_binding(name)
        if isinstance(type_node, (TypeList, TypeDynamicList)):
            return self._list_info_from_type(type_node)

        if type_node is None and self.scope.find_binding_owner(name) is None:
            raise CompilerError(f"Undeclared variable: {name}")
        raise CompilerError(f"'{name}' is not a declared list.")

    def _bind_name(self, name: str, type_node: Optional[TypeNode]) -> None:
        if name in RESERVED_NAMES:
            raise CompilerError(f"{name} is reserved and cannot be declared by the user.")
        if name in self.scope.bindings:
            raise CompilerError(f"Duplicate declaration: {name}")
        self.scope.bindings[name] = type_node

    def _declare_basic(self, name: str, type_node: BasicType) -> None:
        self._bind_name(name, type_node)
        self.emit("DECL", self._ct_type(type_node), name)

    def _declare_list_len_slot(self, list_name: str, dim: int) -> None:
        self._declare_basic(self._list_len_name(list_name, dim), TypeInt())

    def _set_retvar_type(self, type_node: Optional[TypeNode]) -> None:
        self.scope.bindings["retvar"] = type_node
        if isinstance(type_node, (TypeList, TypeDynamicList)):
            self.scope.lists["retvar"] = self._list_info_from_type(type_node)
            return
        self.scope.lists.pop("retvar", None)

    def _try_eval_const_int(self, a: AExp) -> Optional[int]:
        """
        Tries to evaluate the constant integer value of an AExp at compile time.
        Returns None if the expression is not a constant integer or if the expression is not evaluable at compile time.
        """
        try:
            return self._eval_const_int(a)
        except CompilerError:
            return None

    def _eval_const_int(self, a: AExp) -> int:
        """
        Evaluates the constant integer value of an AExp at compile time.
        Required for list literal shape matching and flat index calculation.
        Raises a CompilerError if the expression is not a constant integer.
        """
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

    def _size_ir_from_shape_exprs(self, shape: List[AExp]) -> AExp:
        expr: AExp = IntLit(1)
        for dim_expr in shape:
            expr = ABinOp("*", expr, dim_expr)
        return expr

    def _type_from_shape(self, elem_type: BasicType, shape: Tuple[Optional[int], ...]) -> TypeNode:
        if not shape:
            return elem_type
        if any(dim is None for dim in shape):
            return TypeDynamicList(
                elem_type,
                IntLit(len(shape)),
                [IntLit(dim) if dim is not None else None for dim in shape],
            )
        return TypeList(
            elem_type,
            IntLit(len(shape)),
            [IntLit(dim) for dim in shape if dim is not None],
        )

    def _list_info_from_type(self, type_node: TypeNode) -> _ListInfo:
        """
        Converts a list type to a _ListInfo object.
        """
        if isinstance(type_node, TypeList):
            return _ListInfo(
                elem_type=type_node.elem,
                dimension=type_node.dimension.value,
                shape=tuple(self._try_eval_const_int(dim_expr) for dim_expr in type_node.shape),
            )
        if isinstance(type_node, TypeDynamicList):
            if type_node.shape:
                raw_shape = list(type_node.shape)
                if len(raw_shape) < type_node.dimension.value:
                    raw_shape.extend([None] * (type_node.dimension.value - len(raw_shape)))
            else:
                raw_shape = [None] * type_node.dimension.value
            shape: List[Optional[int]] = []
            for dim_expr in raw_shape:
                # try to resolve the dimension lengths at compile time if possible
                shape.append(None if dim_expr is None else self._try_eval_const_int(dim_expr))
            return _ListInfo(
                elem_type=type_node.elem,
                dimension=type_node.dimension.value,
                shape=tuple(shape),
            )
        raise CompilerError(f"Expected a list type, found {type(type_node).__name__}.")

    def _get_list_expr_info(self, expr: AExp) -> _ListInfo:
        if isinstance(expr, AVar):
            type_node = self._get_declared_type(expr.name)
            if not isinstance(type_node, (TypeList, TypeDynamicList)):
                raise CompilerError(f"Expression '{expr.name}' is not a list.")
            owner = self.scope.find_list_owner(expr.name)
            if owner is not None:
                return owner.lists[expr.name]
            return self._list_info_from_type(type_node)
        if isinstance(expr, ListLit):
            return self._list_info_from_type(self._infer_list_literal_type(expr))
        if isinstance(expr, AIndex):
            info, _base_name, indices = self._resolve_list_access(expr)
            return self._get_sublist_info(info, indices)
        raise CompilerError("Expected a list expression.")

    def _get_sublist_info(self, info: _ListInfo, indices: List[AExp]) -> _ListInfo:
        remaining_shape = info.shape[len(indices):]
        if not remaining_shape:
            raise CompilerError("Indexed expression is not a list.")
        return _ListInfo(info.elem_type, len(remaining_shape), remaining_shape)

    def _are_list_types_compatible(self, actual: _ListInfo, expected: _ListInfo) -> bool:
        """
        Checks if two list types are compatible. 
        Compatible means that the two list types have the same element type and dimension, and the shape of the two lists are possibly same.
        The possibly case is due to the function parameters which are list types with a dynamic dimension.

        Examples:
        List[Int, 2, [3, 2]] and List[Int, 2, [3, 2]] -> True
        List[Int, 2, [3, 2]] and List[Int, 2] -> True
        List[Int, 2, [3, 2]] and List[Int, 2, [3]] -> True
        """
        if actual.elem_type != expected.elem_type or actual.dimension != expected.dimension:
            return False
        for actual_dim, expected_dim in zip(actual.shape, expected.shape):
            if actual_dim is not None and expected_dim is not None and actual_dim != expected_dim:
                return False
        return True

    def _basic_types_compatible(self, actual: TypeNode, expected: TypeNode) -> bool:
        if type(actual) is BasicType:
            return isinstance(expected, (TypeInt, TypeFloat))
        if isinstance(expected, TypeFloat):
            return isinstance(actual, (TypeInt, TypeFloat)) # integers are casted into floats gracefully
        return type(actual) is type(expected)

    def _infer_list_literal_type(self, literal: ListLit) -> TypeList:
        """
        Infers the type of a list literal at compile time.
        Raises a CompilerError if the list literal is empty or if the list literal items do not have a uniform nesting depth.

        Examples:
        [1, 2, 3] -> TypeList(Int, 1, [3])
        [[1, 2], [3, 4]] -> TypeList(Int, 2, [2, 2])
        [[1, 2], [3, 4], [[1], [2]]] -> CompilerError: List literal items must have a uniform nesting depth.
        """
        if not literal.items:
            raise CompilerError("Empty list literals are not supported.")

        first_type = self._infer_imm_type(literal.items[0])
        if isinstance(first_type, (TypeInt, TypeFloat)):
            elem_type = first_type
            for item in literal.items[1:]:
                item_type = self._infer_imm_type(item)
                if not isinstance(item_type, (TypeInt, TypeFloat)):
                    raise CompilerError("List literal items must have a uniform nesting depth.")
                if isinstance(elem_type, TypeFloat) or isinstance(item_type, TypeFloat):
                    elem_type = TypeFloat()
            return TypeList(elem_type, IntLit(1), [IntLit(len(literal.items))])

        nested_info = self._list_info_from_type(first_type)
        elem_type = nested_info.elem_type
        nested_shape = nested_info.shape

        for item in literal.items[1:]:
            item_type = self._infer_imm_type(item)
            if not isinstance(item_type, TypeList):
                raise CompilerError("List literal items must have a uniform nesting depth.")
            item_info = self._list_info_from_type(item_type)
            if item_info.dimension != nested_info.dimension or item_info.shape != nested_shape:
                raise CompilerError("Nested list literal must be rectangular.")
            if item_info.elem_type != elem_type:
                if isinstance(elem_type, TypeInt) and isinstance(item_info.elem_type, TypeFloat):
                    elem_type = TypeFloat()
                elif not (isinstance(elem_type, TypeFloat) and isinstance(item_info.elem_type, TypeInt)):
                    raise CompilerError("Nested list literal elements must have a uniform basic type.")

        shape = [IntLit(len(literal.items))]
        shape.extend(IntLit(dim) for dim in nested_shape if dim is not None)
        return TypeList(elem_type, IntLit(len(shape)), shape)

    def _infer_imm_type(self, imm: Union[IntLit, FloatLit, ListLit]) -> TypeNode:
        if isinstance(imm, IntLit):
            return TypeInt()
        if isinstance(imm, FloatLit):
            return TypeFloat()
        if isinstance(imm, ListLit):
            return self._infer_list_literal_type(imm)

    def _flatten_list_literal(
        self,
        literal: Union[ListLit, IntLit, FloatLit],
        expected_type: TypeNode,
    ) -> List[Union[int, float]]:
        if isinstance(expected_type, TypeList):
            if not isinstance(literal, ListLit):
                raise CompilerError("Nested list literal shape does not match the expected list type.")
            expected_len = self._eval_const_int(expected_type.shape[0])
            if len(literal.items) != expected_len:
                raise CompilerError(
                    f"List literal length mismatch: expected {expected_len}, got {len(literal.items)}."
                )
            flat: List[Union[int, float]] = []
            if expected_type.dimension.value == 1:
                sub_expected: TypeNode = expected_type.elem
            else:
                sub_expected = TypeList(
                    expected_type.elem,
                    IntLit(expected_type.dimension.value - 1),
                    expected_type.shape[1:],
                )
            for item in literal.items:
                flat.extend(self._flatten_list_literal(item, sub_expected))
            return flat

        if isinstance(expected_type, TypeInt):
            if not isinstance(literal, IntLit):
                raise CompilerError("Int lists require integer elements.")
            return [literal.value]

        if isinstance(expected_type, TypeFloat):
            if not isinstance(literal, (IntLit, FloatLit)):
                raise CompilerError("Float lists require numeric elements.")
            return [literal.value]

        raise CompilerError("Unsupported list literal type.")

    def _default_value_expr(self, type_node: TypeNode) -> AExp:
        if isinstance(type_node, TypeInt):
            return IntLit(0)
        if isinstance(type_node, TypeFloat):
            return FloatLit(0.0)
        if isinstance(type_node, TypeList):
            size = self._eval_const_int(type_node.shape[0])
            if type_node.dimension.value == 1:
                sub_default: TypeNode = type_node.elem
            else:
                sub_default = TypeList(
                    type_node.elem,
                    IntLit(type_node.dimension.value - 1),
                    type_node.shape[1:],
                )
            return ListLit([self._default_value_expr(sub_default) for _ in range(size)])
        if isinstance(type_node, TypeDynamicList):
            raise CompilerError("Functions returning dynamically-sized lists must end with an explicit return.")
        raise CompilerError("Unsupported function return type for the return rule.")

    def _extract_list_access(self, expr: AIndex) -> Tuple[str, List[AExp]]:
        """
        Extracts the list name and indices from a list access expression.
        """
        indices: List[AExp] = []
        cur: AExp = expr
        while isinstance(cur, AIndex):
            indices.append(cur.index)
            cur = cur.base
        if not isinstance(cur, AVar):
            raise CompilerError("Only direct list variables can be indexed.")
        indices.reverse()
        return cur.name, indices

    def _resolve_list_access(self, expr: AIndex) -> Tuple[_ListInfo, str, List[AExp]]:
        base_name, indices = self._extract_list_access(expr)
        info = self._get_list_info(base_name)
        if len(indices) > info.dimension:
            raise CompilerError(
                f"List '{base_name}' of rank {info.dimension} cannot be indexed with {len(indices)} indices."
            )
        return info, base_name, indices

    def _build_flat_index_expr(self, base_name: str, info: _ListInfo, indices: List[AExp]) -> AExp:
        """
        Generates the flat index expression for a list access. Result is an AExp that evaluates to the flat index to support
        indexing with values which are determined at runtime.
        """
        if not indices:
            raise CompilerError("Internal: cannot build a flat index without indices.")

        flat_expr: Optional[AExp] = None
        for pos, idx_expr in enumerate(indices):
            term: AExp = idx_expr
            for dim in range(pos + 2, info.dimension + 1):
                term = ABinOp("*", term, AVar(self._list_len_name(base_name, dim)))
            flat_expr = term if flat_expr is None else ABinOp("+", flat_expr, term)
        return flat_expr

    def _get_type_list_access(self, info: _ListInfo, indices: List[AExp]) -> TypeNode:
        if len(indices) == info.dimension:
            return info.elem_type
        return self._type_from_shape(info.elem_type, info.shape[len(indices):])

    def _type_of_aexp(self, expr: AExp) -> TypeNode:
        if isinstance(expr, IntLit):
            return TypeInt()
        if isinstance(expr, FloatLit):
            return TypeFloat()
        if isinstance(expr, ListLit):
            return self._infer_list_literal_type(expr)
        if isinstance(expr, AVar):
            return self._get_declared_type(expr.name)
        if isinstance(expr, ALen):
            self._get_declared_type(expr.name)
            return TypeInt()
        if isinstance(expr, AIndex):
            info, _base_name, indices = self._resolve_list_access(expr)
            return self._get_type_list_access(info, indices)
        if isinstance(expr, AUnOp):
            inner = self._type_of_aexp(expr.rhs)
            if not isinstance(inner, (TypeInt, TypeFloat)):
                raise CompilerError("Unary '-' can only be applied to basic numeric expressions.")
            return inner
        if isinstance(expr, ABinOp):
            left = self._type_of_aexp(expr.left)
            right = self._type_of_aexp(expr.right)
            if not isinstance(left, (TypeInt, TypeFloat)) or not isinstance(right, (TypeInt, TypeFloat)):
                raise CompilerError("Binary arithmetic operators can only be applied to basic numeric expressions.")
            if isinstance(left, TypeFloat) or isinstance(right, TypeFloat):
                return TypeFloat()
            return TypeInt()
        if isinstance(expr, FuncCall):
            func_info = self.functions.get(expr.name)
            if func_info is None:
                raise CompilerError(f"Undefined function: {expr.name}")
            return func_info[0].return_type
        if isinstance(expr, ARead):
            return BasicType()
        raise CompilerError(f"Unknown arithmetic expression node: {type(expr).__name__}")

    def _ct_type(self, type_node: BasicType) -> str:
        if isinstance(type_node, TypeInt):
            return "INT"
        if isinstance(type_node, TypeFloat):
            return "FLOAT"
        raise CompilerError(f"Unsupported non-basic type in DECL: {type(type_node).__name__}")

    def _ir_list_type(self, elem_type: BasicType) -> str:
        """
        Converts a basic type to the corresponding IR list type. Required for list declaration.
        """
        if isinstance(elem_type, TypeInt):
            return "LIST[INT]"
        if isinstance(elem_type, TypeFloat):
            return "LIST[FLOAT]"
        raise CompilerError(f"Unsupported list element type: {type(elem_type).__name__}")

    def _emit_list_literal_packet(self, literal: ListLit, expected: Optional[_ListInfo] = None) -> _ListInfo:
        literal_type = self._infer_list_literal_type(literal)
        actual_info = self._list_info_from_type(literal_type)
        if expected is not None and not self._are_list_types_compatible(actual_info, expected):
            raise CompilerError("List literal does not match the expected list type.")
        flat_values = self._flatten_list_literal(literal, literal_type)
        for value in flat_values:
            self.emit("PUSH", value)
        self.emit("PUSH", len(flat_values))
        return actual_info

    def _emit_static_sublist_packet(self, expr: AIndex, expected: Optional[_ListInfo] = None) -> _ListInfo:
        info, base_name, indices = self._resolve_list_access(expr)
        actual_info = self._get_sublist_info(info, indices)
        if expected is not None and not self._are_list_types_compatible(actual_info, expected):
            raise CompilerError("List expression does not match the expected list type.")
        flat_size = actual_info.flat_size
        if flat_size is None:
            raise CompilerError("Cannot use a dynamically-sized sub-list as a first-class value.")
        base_index = self._build_flat_index_expr(base_name, info, indices)
        for offset in range(flat_size):
            index_expr = base_index if offset == 0 else ABinOp("+", base_index, IntLit(offset))
            self.CA(index_expr)
            self.emit("LLOAD", base_name)
        self.emit("PUSH", flat_size)
        return actual_info

    def _compile_list_value(self, expr: AExp, expected: _ListInfo) -> None:
        if isinstance(expr, ListLit):
            self._emit_list_literal_packet(expr, expected)
            return
        if isinstance(expr, AVar):
            actual = self._get_list_expr_info(expr)
            if not self._are_list_types_compatible(actual, expected):
                raise CompilerError(f"List expression '{expr.name}' does not match the expected list type.")
            self.emit("LOAD", expr.name)
            return
        if isinstance(expr, AIndex):
            self._emit_static_sublist_packet(expr, expected)
            return
        raise CompilerError("Only list variables, list literals, or static sub-lists can be used here.")

    def _compile_rhs_list_element(self, expr: AExp, offset: int) -> None:
        """
        Handles the compilation of a list element on the right-hand side of a sub-list assignment.
        
        Examples:
        lst: List[Int, 2, [3, 2]]; lst2: List[Int, 2, [3, 2]]; lst3: List[Int, 1, [2]]
        lst[0] := [1, 2, 3]
        lst[0] := lst2[0]
        lst[1] := lst3
        """
        if isinstance(expr, ListLit):
            literal_type = self._infer_list_literal_type(expr)
            flat_values = self._flatten_list_literal(expr, literal_type)
            self.emit("PUSH", flat_values[offset])
            return
        if isinstance(expr, AVar):
            self.emit("PUSH", offset)
            self.emit("LLOAD", expr.name)
            return
        if isinstance(expr, AIndex):
            info, base_name, indices = self._resolve_list_access(expr)
            sub_info = self._get_sublist_info(info, indices)
            if sub_info.flat_size is None:
                raise CompilerError("Cannot assign from a dynamically-sized sub-list.")
            base_index = self._build_flat_index_expr(base_name, info, indices)
            index_expr = base_index if offset == 0 else ABinOp("+", base_index, IntLit(offset))
            self.CA(index_expr)
            self.emit("LLOAD", base_name)
            return
        raise CompilerError("Unsupported list expression on the right-hand side of sub-list assignment.")

    def CA(self, expr: AExp) -> None:
        if isinstance(expr, IntLit):
            self.emit("PUSH", expr.value)
            return
        if isinstance(expr, FloatLit):
            self.emit("PUSH", expr.value)
            return
        if isinstance(expr, ListLit):
            self._emit_list_literal_packet(expr)
            return
        if isinstance(expr, AVar):
            self._get_declared_type(expr.name)
            self.emit("LOAD", expr.name)
            return
        if isinstance(expr, ALen):
            self._get_declared_type(expr.name)
            self.emit("LEN", expr.name)
            return
        if isinstance(expr, AIndex):
            info, base_name, indices = self._resolve_list_access(expr)
            if len(indices) != info.dimension:
                raise CompilerError(
                    f"Sub-list '{base_name}[..]' cannot appear as a scalar arithmetic expression."
                )
            self.CA(self._build_flat_index_expr(base_name, info, indices))
            self.emit("LLOAD", base_name)
            return
        if isinstance(expr, AUnOp):
            if expr.op != "-":
                raise CompilerError(f"Unsupported unary operator in arithmetic expression: {expr.op}")
            self.emit("PUSH", 0)
            self.CA(expr.rhs)
            self.emit("SUB")
            return
        if isinstance(expr, ABinOp):
            self.CA(expr.left)
            self.CA(expr.right)
            if expr.op == "+":
                self.emit("ADD")
                return
            if expr.op == "-":
                self.emit("SUB")
                return
            if expr.op == "*":
                self.emit("MUL")
                return
            if expr.op == "/":
                self.emit("fDIV" if isinstance(self._type_of_aexp(expr), TypeFloat) else "iDIV")
                return
            raise CompilerError(f"Unsupported binary operator in arithmetic expression: {expr.op}")
        if isinstance(expr, ARead):
            self.emit("READ")
            return
        raise CompilerError(f"Unsupported arithmetic expression: {type(expr).__name__}")

    def CB(self, expr: BExp) -> None:
        if isinstance(expr, BBool):
            self.emit("PUSH", 1 if expr.value else 0)
            return
        if isinstance(expr, BTruthy):
            self.CA(expr.aexp)
            self.emit("PUSH", 0)
            self.emit("EQ")
            self.emit("NEG")
            return
        if isinstance(expr, BCompare):
            self.CA(expr.left)
            self.CA(expr.right)
            if expr.op == "=":
                self.emit("EQ")
                return
            if expr.op == "<":
                self.emit("LT")
                return
            if expr.op == "<=":
                self.emit("LE")
                return
            if expr.op == ">":
                self.emit("GT")
                return
            if expr.op == ">=":
                self.emit("GE")
                return
            raise CompilerError(f"Unsupported comparison operator: {expr.op}")
        if isinstance(expr, BNot):
            self.CB(expr.rhs)
            self.emit("NEG")
            return
        if isinstance(expr, BBinOp):
            self.CB(expr.left)
            self.CB(expr.right)
            if expr.op == "and":
                self.emit("AND")
                return
            if expr.op == "or":
                self.emit("OR")
                return
            if expr.op == "xor":
                self.emit("XOR")
                return
            raise CompilerError(f"Unsupported boolean operator: {expr.op}")
        raise CompilerError(f"Unsupported boolean expression: {type(expr).__name__}")

    def C_stmt(self, stmt: Stmt) -> None:
        if isinstance(stmt, Decl):
            self._compile_decl(stmt)
            self.emit("STEP")
            return
        if isinstance(stmt, Assign):
            self._compile_assign(stmt)
            self.emit("STEP")
            return
        if isinstance(stmt, ListAssign):
            self._compile_list_assign(stmt)
            self.emit("STEP")
            return
        if isinstance(stmt, Pass):
            self.emit("NOOP")
            return
        if isinstance(stmt, SpecAnnot):
            self.raw_specs[stmt.spec.spec_id] = stmt.spec
            self.emit("VERI", stmt.spec.spec_id, stmt.spec.text)
            return
        if isinstance(stmt, If):
            self._compile_if(stmt)
            return
        if isinstance(stmt, While):
            self._compile_while(stmt)
            return
        if isinstance(stmt, Return):
            self._compile_return(stmt)
            return
        if isinstance(stmt, FuncCall):
            self._compile_call(stmt)
            return
        if isinstance(stmt, Write):
            self._compile_write(stmt)
            return
        if isinstance(stmt, WriteLine):
            self._compile_writeline(stmt)
            return
        raise CompilerError(f"Unsupported statement: {type(stmt).__name__}")

    def _compile_decl(self, decl: Decl) -> None:
        name = decl.name
        type_node = decl.type_node

        if isinstance(type_node, (TypeInt, TypeFloat)):
            self._declare_basic(name, type_node)
            return

        if isinstance(type_node, TypeList):
            self._bind_name(name, type_node)
            self.scope.lists[name] = self._list_info_from_type(type_node)
            # _lst_dim_i generation
            for dim, shape_expr in enumerate(type_node.shape, start=1):
                self._declare_list_len_slot(name, dim)
                self.CA(shape_expr)
                self.emit("STORE", self._list_len_name(name, dim))
            self.CA(self._size_ir_from_shape_exprs(type_node.shape)) # flat size
            self.emit("LDECL", self._ir_list_type(type_node.elem), name)
            return

        raise CompilerError(f"Unsupported declaration type: {type(type_node).__name__}")

    def _compile_assign(self, stmt: Assign) -> None:
        target_type = self._get_declared_type(stmt.name)

        if isinstance(target_type, (TypeInt, TypeFloat)):
            source_type = self._type_of_aexp(stmt.aexp)
            if not self._basic_types_compatible(source_type, target_type):
                raise CompilerError(f"Type mismatch in assignment to '{stmt.name}'.")
            self.CA(stmt.aexp)
            self.emit("STORE", stmt.name)
            return

        if isinstance(target_type, (TypeList, TypeDynamicList)):
            expected = self._get_list_expr_info(AVar(stmt.name))
            actual = self._get_list_expr_info(stmt.aexp)
            if not self._are_list_types_compatible(actual, expected):
                raise CompilerError(f"List assignment to '{stmt.name}' does not match the declared type.")
            self._compile_list_value(stmt.aexp, expected)
            self.emit("STORE", stmt.name)
            return

        raise CompilerError(f"Unsupported assignment target type: {type(target_type).__name__}")

    def _compile_list_assign(self, stmt: ListAssign) -> None:
        info, base_name, indices = self._resolve_list_access(stmt.target)
        if len(indices) == info.dimension: # we are just assigning to an entry which has a basic type
            value_type = self._type_of_aexp(stmt.aexp)
            if not self._basic_types_compatible(value_type, info.elem_type):
                raise CompilerError("Scalar list assignment requires a basic value of the element type.")
            self.CA(stmt.aexp)
            self.CA(self._build_flat_index_expr(base_name, info, indices))
            self.emit("LSTORE", base_name)
            return

        target_sub_info = self._get_sublist_info(info, indices) # whole list case is impossible by parser (handled by regular assign)
        target_size = target_sub_info.flat_size
        if target_size is None:
            raise CompilerError("Cannot assign to a dynamically-sized sub-list.")

        actual_info = self._get_list_expr_info(stmt.aexp)
        if not self._are_list_types_compatible(actual_info, target_sub_info):
            raise CompilerError("Sub-list assignment does not match the target list type.")

        base_index = self._build_flat_index_expr(base_name, info, indices)
        for offset in range(target_size):
            self._compile_rhs_list_element(stmt.aexp, offset)
            index_expr = base_index if offset == 0 else ABinOp("+", base_index, IntLit(offset))
            self.CA(index_expr)
            self.emit("LSTORE", base_name)

    def _compile_block(self, stmts: List[Stmt]) -> None:
        self.emit("CSCOPE")
        self._create_scope()
        self.C_stmt_seq(stmts)
        self._leave_scope()
        self.emit("PSCOPE")

    def _compile_if(self, stmt: If) -> None:
        self.CB(stmt.cond)
        jz_patch = _PatchRef(self.emit("JZ", -1))
        self._compile_block(stmt.then_s)

        if is_empty_stmt_seq(stmt.else_s): # is there is no else block, just jump to the end of the if block
            noop_pc = self.emit("NOOP")
            self.patch_jump(jz_patch, noop_pc)
            return

        goto_patch = _PatchRef(self.emit("GOTO", -1))
        else_start_pc = self.pc()
        self._compile_block(stmt.else_s or [])
        noop_pc = self.emit("NOOP")
        self.patch_jump(jz_patch, else_start_pc)
        self.patch_jump(goto_patch, noop_pc)

    def _compile_while(self, stmt: While) -> None:
        cond_pc = self.pc()
        self.CB(stmt.cond)
        jz_patch = _PatchRef(self.emit("JZ", -1))
        self._compile_block(stmt.body)
        self.emit("GOTO", cond_pc)
        noop_pc = self.emit("NOOP")
        self.patch_jump(jz_patch, noop_pc)

    def _compile_return(self, stmt: Return) -> None:
        if self.current_return_type is None:
            raise CompilerError("Internal: return statement outside of a function.")

        expected_type = self.current_return_type
        if isinstance(expected_type, (TypeList, TypeDynamicList)):
            expected = self._list_info_from_type(expected_type)
            actual = self._get_list_expr_info(stmt.value)
            if not self._are_list_types_compatible(actual, expected):
                raise CompilerError("Returned list value does not match the declared function return type.")
            self._compile_list_value(stmt.value, expected)
            self.emit("RET")
            return

        actual_type = self._type_of_aexp(stmt.value)
        if not self._basic_types_compatible(actual_type, expected_type):
            raise CompilerError("Returned value does not match the declared function return type.")
        self.CA(stmt.value)
        self.emit("RET")

    def _compile_call(self, call: FuncCall) -> None:
        func_info = self.functions.get(call.name)
        if func_info is None:
            raise CompilerError(f"Undefined function: {call.name}")

        func_decl, _ = func_info
        if len(call.args) != len(func_decl.params):
            raise CompilerError(
                f"Function '{call.name}' expects {len(func_decl.params)} arguments, got {len(call.args)}."
            )

        for param, arg in zip(func_decl.params, call.args):
            if isinstance(param.type_node, (TypeList, TypeDynamicList)):
                expected = self._list_info_from_type(param.type_node)
                actual = self._get_list_expr_info(arg)
                if not self._are_list_types_compatible(actual, expected):
                    raise CompilerError(f"Argument for parameter '{param.name} : {param.type_node}' does not match the list type provided {actual}.")
                self._compile_list_value(arg, expected)
            else:
                actual_type = self._type_of_aexp(arg)
                if not self._basic_types_compatible(actual_type, param.type_node):
                    raise CompilerError(f"Argument for parameter '{param.name} : {param.type_node}' does not match the type provided {actual_type}.")
                self.CA(arg)

        self.emit("CALL", call.name)
        self._set_retvar_type(func_decl.return_type)

    def _compile_write(self, stmt: Write) -> None:
        self.CA(stmt.aexp)
        t = self._type_of_aexp(stmt.aexp)
        if isinstance(t, (TypeList, TypeDynamicList)):
            self.emit("LWRITE")
        else:
            self.emit("WRITE")

    def _compile_writeline(self, stmt: WriteLine) -> None:
        self.CA(stmt.aexp)
        t = self._type_of_aexp(stmt.aexp)
        if isinstance(t, (TypeList, TypeDynamicList)):
            self.emit("LWRITELN")
        else:
            self.emit("WRITELN")

    def C_stmt_seq(self, stmts: List[Stmt]) -> None:
        for stmt in stmts:
            self.C_stmt(stmt)

    def _register_functions(self, program: Program) -> None:
        for func in program.func_decls:
            if func.name in self.functions:
                raise CompilerError(f"Duplicate function declaration: {func.name}")
            self.functions[func.name] = (func, -1)

    def _declare_static_list_param(self, name: str, type_node: TypeList) -> None:
        self._bind_name(name, type_node)
        info = self._list_info_from_type(type_node)
        self.scope.lists[name] = info
        for dim, shape_expr in enumerate(type_node.shape, start=1): # declare length slots for each dimension
            self._declare_list_len_slot(name, dim)
            self.CA(shape_expr)
            self.emit("STORE", self._list_len_name(name, dim))
        flat_size = info.flat_size
        if flat_size is None: # if the list is dynamically sized, raise an error
            raise CompilerError("Function list parameters must have statically known sizes.")
        self.emit("PUSH", flat_size)
        self.emit("LDECL", self._ir_list_type(type_node.elem), name)
        self.emit("STORE", name)

    def _declare_dynamic_list_param(self, name: str, type_node: TypeDynamicList) -> None:
        if type_node.dimension.value != 1:
            raise CompilerError("Only rank-1 variable-length list parameters are supported.")
        self._bind_name(name, type_node)
        self.scope.lists[name] = self._list_info_from_type(type_node)
        self._declare_list_len_slot(name, 1)
        self.emit("STORE", self._list_len_name(name, 1))
        self.emit("LOAD", self._list_len_name(name, 1))
        self.emit("LDECL", self._ir_list_type(type_node.elem), name)
        self.emit("LOAD", self._list_len_name(name, 1))
        self.emit("STORE", name)

    def _compile_function_decl(self, func: FunctionDecl) -> None:
        old_scope = self.scope
        old_return_type = self.current_return_type
        entry_pc = self.pc()
        self.functions[func.name] = (func, entry_pc)

        self.emit("FUNCENV", func.name, f"{func.return_type}")
        self.scope = _ScopeFrame()
        self.scope.bindings["retvar"] = None
        self.current_return_type = func.return_type

        # Parameters are pushed left-to-right by the caller, so bind them from the
        # top of the stack back toward the first argument.
        for param in reversed(func.params):
            if isinstance(param.type_node, (TypeInt, TypeFloat)):
                self._declare_basic(param.name, param.type_node)
                self.emit("STORE", param.name) # fetch parameter value from stack
                continue
            if isinstance(param.type_node, TypeList):
                self._declare_static_list_param(param.name, param.type_node)
                continue
            if isinstance(param.type_node, TypeDynamicList):
                self._declare_dynamic_list_param(param.name, param.type_node)
                continue
            raise CompilerError(f"Unsupported parameter type: {type(param.type_node).__name__}")
        self.emit("STEP") # step after initializing parameters

        body = list(func.body) # copy for appending return
        if not stmt_seq_guarantees_return(body): # if the function does not return a value, add a default return value
            body.append(Return(self._default_value_expr(func.return_type)))
        self.C_stmt_seq(body)
        self.scope = old_scope # restore old scope
        self.current_return_type = old_return_type

    def compile_program(self, program: Program) -> List[IRInstr]:
        self._register_functions(program)

        # program must jump to the main statement sequence
        entry_jump: Optional[_PatchRef] = None
        if program.func_decls: # if there are any functions
            entry_jump = _PatchRef(self.emit("GOTO", -1))

        for func in program.func_decls:
            self._compile_function_decl(func)

        if entry_jump is not None:
            self.patch_jump(entry_jump, self.pc())

        self.C_stmt_seq(program.stmt_seq)

        return self.code

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
    arg_parser.add_argument("input", type=Path, help="Path to the .svi source file")
    arg_parser.add_argument("-o", "--output", type=Path, help="Path for the output IR file")
    args = arg_parser.parse_args()

    output = args.output if args.output is not None else args.input.with_suffix(".svir")
    with open(args.input, "r", encoding="utf-8") as f:
        src = f.read()
    ir_text = compile_selveri_source_to_ir_text(src)
    with open(output, "w", encoding="utf-8") as f:
        f.write(ir_text)
