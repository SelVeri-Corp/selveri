from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union, Tuple

from parser import (
    parse_selveri,
    Program,
    Stmt, Decl, Assign, ListAssign, Pass, If, While, SpecAnnot,
    TypeNode, TypeInt, TypeFloat, TypeList,
    AExp, IntLit, FloatLit, ListLit, AVar, ALen, AIndex, AUnOp, ABinOp,
    BExp, BBool, BNot, BBinOp, BCompare, BTruthy,
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
    def rank(self) -> int:
        return len(self.shape)

    @property
    def top_level_len(self) -> int:
        return self.shape[0]


# Is empty helper
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
    - Maintains one scope for compile-time typing.
    - Emits labeled IR where label == instruction index.
    """

    def __init__(self) -> None:
        # All declared names, including high-level lists and lowered flat elements.
        self.scope: Dict[str, TypeNode] = {}
        # List metadata for flattened lowering.
        self.lists: Dict[str, _ListInfo] = {}
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

    def require_declared(self, name: str) -> None:
        if name not in self.scope:
            raise CompilerError(f"Undeclared variable: {name}")

    def _get_declared_type(self, name: str) -> TypeNode:
        t = self.scope.get(name)
        if t is None:
            raise CompilerError(f"Undeclared variable: {name}")
        return t

    def _eval_const_int(self, a: AExp) -> int:
        # evaluates the constant integer value of an AExp at compile time
        # required for list literal shape matching and flat index calculation
        if isinstance(a, IntLit):
            return a.value
        if isinstance(a, FloatLit):
            raise CompilerError("Expected compile-time integer expression, found float literal.")
        if isinstance(a, ALen):
            info = self.lists.get(a.name)
            if info is None:
                raise CompilerError(f"len({a.name}) used but '{a.name}' is not a declared list.")
            return info.top_level_len # len is calculated at compile time
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
        shape: List[int] = []
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
            strides.append(running)
            running *= dim
        strides.reverse()
        flat_size = running # the total number of elements in the list (shape[0] * shape[1] * ... * shape[n-1])
        return _ListInfo(elem_type=elem_t, shape=tuple(shape), strides=tuple(strides), flat_size=flat_size)

    def _flat_list_decl_type(self, info: _ListInfo) -> TypeList:
        return TypeList(info.elem_type, IntLit(info.flat_size)) # the type of the list is the type of the elements and the size is the total number of elements

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
        info = self.lists.get(base_name)
        if info is None:
            raise CompilerError(f"Indexing '{base_name}[..]' but '{base_name}' is not a declared list.")
        if len(indices) > info.rank:
            raise CompilerError(
                f"Flattened list '{base_name}' of rank {info.rank} "
                f"cannot be indexed with {len(indices)} indices."
            )
        return info, base_name, indices

    def _get_type_list_access(self, info: _ListInfo, indices: List[AExp]) -> TypeNode:
        # if fully indexed, we reach the numeric element type
        if len(indices) == info.rank:
            return info.elem_type
        # otherwise, we return a (lower-rank) list type corresponding to the remaining dimensions after the applied indices
        remaining_shape = info.shape[len(indices):]
        elem_t: TypeNode = info.elem_type
        # rebuild nested TypeList from inner-most dimension outwards
        for dim in reversed(remaining_shape):
            elem_t = TypeList(elem_t, IntLit(dim))
        return elem_t # the type of the list after the applied indices

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

    # ---------- type checks ----------
    def _type_of_aexp(self, a: AExp) -> TypeNode:
        if isinstance(a, IntLit):
            return TypeInt()
        if isinstance(a, FloatLit):
            return TypeFloat()
        if isinstance(a, ListLit):
            raise CompilerError("not supported")
        if isinstance(a, AVar):
            t = self.scope.get(a.name)
            if t is None:
                raise CompilerError(f"Undeclared variable in AExp: {a.name}")
            if isinstance(t, TypeList):
                raise CompilerError(f"Using list variable '{a.name}' as numeric is not supported.")
            return t
        if isinstance(a, ALen):
            if a.name not in self.lists:
                raise CompilerError(f"len({a.name}) used but '{a.name}' is not a declared list.")
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

    # ---------- CA: arithmetic compilation ----------
    def CA(self, a: AExp) -> None:
        # CA(x) = PUSH x if x is Imm
        if isinstance(a, IntLit):
            self.emit("PUSH", a.value)
            return
        if isinstance(a, FloatLit):
            self.emit("PUSH", a.value)
            return

        if isinstance(a, ListLit):
            raise CompilerError("not supported")

        if isinstance(a, AVar):
            self.require_declared(a.name)
            t = self.scope[a.name]
            if isinstance(t, TypeList):
                raise CompilerError(f"Cannot load whole list '{a.name}'. Use indexing.")
            self.emit("PUSH", a.name)
            return

        if isinstance(a, ALen):
            info = self.lists.get(a.name)
            if info is None:
                raise CompilerError(f"len({a.name}) but '{a.name}' is not a list.")
            self.emit("PUSH", info.top_level_len) # len is resolved at compile time
            return

        if isinstance(a, AIndex):
            info, base_name, indices = self._resolve_list_access(a)
            if len(indices) != info.rank:
                raise CompilerError(
                    f"Cannot load whole list expression '{base_name}[..]' as a numeric value; "
                    f"provide exactly {info.rank} indices."
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

        raise CompilerError(f"Unsupported statement: {type(s).__name__}")

    def _compile_decl(self, d: Decl) -> None:
        name = d.name
        if name in self.scope:
            raise CompilerError(f"Duplicate declaration: {name}")

        t = d.type_node
        self.scope[name] = t

        if isinstance(t, TypeInt):
            self.emit("DECL", "INT", name)
            return

        if isinstance(t, TypeFloat):
            self.emit("DECL", "FLOAT", name)
            return

        if isinstance(t, TypeList):
            info = self._flatten_list_type(t)
            self.lists[name] = info
            self.emit("DECL", self._flat_list_decl_type(info), name)

            for i in range(info.flat_size):
                elem_name = self.mangle_list_elem(name, i)
                if elem_name in self.scope:
                    raise CompilerError(f"Internal: duplicate lowered name: {elem_name}")
                self.scope[elem_name] = info.elem_type
            return

        raise CompilerError(f"Unsupported type in declaration: {type(t).__name__}")

    def _compile_assign(self, a: Assign) -> None:
        name = a.name
        t = self._type_of_var_for_store(name)
        if isinstance(t, TypeList): # this is the whole list assignment like lst := [1, 2, 3]
            if not isinstance(a.aexp, ListLit):
                raise CompilerError(f"Whole-list assignment for '{name}' requires a list literal.")
            flat_values = self._flatten_list_literal(a.aexp, t)
            info = self.lists.get(name)
            if info is None:
                raise CompilerError(f"Internal: missing list metadata for '{name}'.")
            if len(flat_values) != info.flat_size:
                raise CompilerError(
                    f"Internal: flattened literal size mismatch for '{name}': "
                    f"{len(flat_values)} != {info.flat_size}"
                )
            for idx, value in enumerate(flat_values): # TODO: change this into STORE instructions with mangling names
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
        remaining_rank = elem_info.rank - len(indices)
        if remaining_rank < 0:
            # should be caught earlier in _resolve_list_access
            raise CompilerError("Too many indices for list assignment.")

        # case 1: fully indexed element assignment (scalar store)
        if remaining_rank == 0:
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

    def _compile_if(self, s: If) -> None:
        self.CB(s.cond) # condition
        jz_patch = _PatchRef(self.emit("JZ", -1))  # placeholder

        # then
        self.C_stmt_seq(s.then_s) # then body

        if is_empty_stmt_seq(s.else_s):
            noop_pc = self.emit("NOOP")
            self.patch_jump(jz_patch, noop_pc)
            return

        goto_patch = _PatchRef(self.emit("GOTO", -1))  # placeholder to skip else
        else_start_pc = self.pc()
        self.C_stmt_seq(s.else_s) # else body
        noop_pc = self.emit("NOOP")

        self.patch_jump(jz_patch, else_start_pc)
        self.patch_jump(goto_patch, noop_pc)

    def _compile_while(self, s: While) -> None:
        # C(while b do S od) = CB(b) : JZ pcNOOP : C(S) : GOTO pcb : NOOP
        pcb = self.pc()
        self.CB(s.cond)
        jz_patch = _PatchRef(self.emit("JZ", -1))  # to NOOP at end
        self.C_stmt_seq(s.body)
        self.emit("GOTO", pcb)
        noop_pc = self.emit("NOOP")
        self.patch_jump(jz_patch, noop_pc)

    # ---------- program ----------
    def C_stmt_seq(self, s: List[Stmt]) -> None:
        for stmt in s:
            self.C_stmt(stmt)

    def compile_program(self, p: Program) -> List[IRInstr]:
        self.C_stmt_seq(p.stmt_seq)
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