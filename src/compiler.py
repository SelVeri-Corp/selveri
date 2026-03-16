from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union, Tuple

from parser import (
    parse_selveri,
    Program,
    Stmt, Decl, Assign, ListAssign, Pass, If, While, SpecAnnot,
    ExprA, ExprB,
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
    label: int
    op: str
    args: Tuple[Union[str, int, float], ...] = ()

    def render(self) -> str:
        return f"{self.label}: {self.op} " + ", ".join(str(a) for a in self.args)


# Patch reference for jumps
# As we do not know the target address of the jump until after the code is generated, we need to patch the jump address later.
@dataclass
class _PatchRef:
    idx: int  # instruction index to patch


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
    - Maintains a scope for compile-time typing to select STORE and iDIV/fDIV.
    - Emits labeled IR where label == instruction index.
    """

    def __init__(self) -> None:
        # High-level variable types (including list meta)
        self.scope_hl: Dict[str, TypeNode] = {}
        # IR-level scalar variables (including lowered list elements)
        self.scope_ir: Dict[str, TypeNode] = {}
        # List metadata: name -> (elem_type, size)
        self.lists: Dict[str, Tuple[TypeNode, int]] = {}
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
        if name not in self.scope_hl and name not in self.scope_ir:
            raise CompilerError(f"Undeclared variable: {name}")

    # ---------- type checks ----------
    def _type_of_aexp(self, a: AExp) -> TypeNode:
        if isinstance(a, IntLit):
            return TypeInt()
        if isinstance(a, FloatLit):
            return TypeFloat()
        if isinstance(a, ListLit):
            raise CompilerError("not supported")
        if isinstance(a, AVar):
            t = self.scope_hl.get(a.name) or self.scope_ir.get(a.name)
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
            raise CompilerError("not supported")
            # if a.name not in self.lists:
            #     raise CompilerError(f"Indexing '{a.name}[..]' but '{a.name}' is not a declared list.")
            # elem_t, _n = self.lists[a.name]
            # return elem_t
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
        t = self.scope_hl.get(name) or self.scope_ir.get(name)
        if t is None:
            raise CompilerError(f"Undeclared variable: {name}")
        if isinstance(t, TypeList):
            raise CompilerError(f"Cannot store into whole list variable '{name}' (only elements).")
        return t

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

        # CA(x) = LOAD x if x is Var   (doc says STORE, but IR uses LOAD)
        if isinstance(a, AVar):
            self.require_declared(a.name)
            # If it's a lowered list element, it's in scope_ir.
            if a.name in self.scope_ir:
                self.emit("LOAD", a.name)
                return
            # If it's a high-level scalar
            t = self.scope_hl.get(a.name)
            if isinstance(t, TypeList):
                raise CompilerError(f"Cannot load whole list '{a.name}'. Use indexing.")
            self.emit("PUSH", a.name)
            return

        if isinstance(a, ALen):
            if a.name not in self.lists:
                raise CompilerError(f"len({a.name}) but '{a.name}' is not a list.")
            _elem_t, n = self.lists[a.name]
            self.emit("PUSH", n)
            return

        if isinstance(a, AIndex):
            raise CompilerError("not supported")
            # if a.name not in self.lists:
            #     raise CompilerError(f"Indexing '{a.name}[..]' but '{a.name}' is not a list.")
            # # Require compile-time constant index due to IR limitations
            # if not isinstance(a.index, IntLit):
            #     raise CompilerError(
            #         f"List index must be an int literal in IR-lowering mode: {a.name}[...]"
            #     )
            # idx = a.index.value
            # _elem_t, n = self.lists[a.name]
            # if idx < 0 or idx >= n:
            #     raise CompilerError(f"Index out of bounds at compile time: {a.name}[{idx}], len={n}")
            # elem_name = self.mangle_list_elem(a.name, idx)
            # self.emit("LOAD", elem_name)
            # return

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
        if name in self.scope_hl or name in self.scope_ir:
            raise CompilerError(f"Duplicate declaration: {name}")

        t = d.type_node
        self.scope_hl[name] = t

        if isinstance(t, TypeInt):
            self.scope_ir[name] = t
            self.emit("DECL", "INT", name)
            return

        if isinstance(t, TypeFloat):
            self.scope_ir[name] = t
            self.emit("DECL", "FLOAT", name)
            return

        if isinstance(t, TypeList):
            # Lower list into scalar variables: name[0] ... name[n-1]
            # Requires size to be compile-time int literal.
            if not isinstance(t.size, IntLit):
                raise CompilerError("List size must be an int literal to lower to IR.")
            n = t.size.value
            if n < 0:
                raise CompilerError("List size cannot be negative.")
            elem_t = t.elem
            self.lists[name] = (elem_t, n)
            self.emit("DECL", t, name)

            # Declare element slots
            for i in range(n):
                elem_name = self.mangle_list_elem(name, i)
                if elem_name in self.scope_ir:
                    raise CompilerError(f"Internal: duplicate lowered name: {elem_name}")
                self.scope_ir[elem_name] = elem_t
            return

        raise CompilerError(f"Unsupported type in declaration: {type(t).__name__}")

    def _compile_assign(self, a: Assign) -> None:
        name = a.name
        t = self._type_of_var_for_store(name)
        # Only numeric assigns supported (ExprA or ExprB are possible from parser).
        # We allow storing numeric AExp, and also allow storing BExp result as int
        # (0/1) if the target variable is Int.
        if isinstance(a.expr, ExprA):
            self.CA(a.expr.aexp)
        elif isinstance(a.expr, ExprB):
            # compile boolean expression to 0/1 on stack
            if not isinstance(t, TypeInt):
                raise CompilerError("Storing boolean into Float is not allowed.")
            self.CB(a.expr.bexp)
        else:
            raise CompilerError("Unknown Expr variant.")

        if not isinstance(t, (TypeInt, TypeFloat)):
            raise CompilerError(f"Unsupported store target type for {name}: {type(t).__name__}")
        self.emit("STORE", name)

    def _compile_list_assign(self, la: ListAssign) -> None:
        raise CompilerError("not supported")
        # base = la.name
        # if base not in self.lists:
        #     raise CompilerError(f"List assignment to '{base}[..]' but '{base}' is not a list.")
        # elem_t, n = self.lists[base]

        # # Require constant index due to IR limitations
        # if not isinstance(la.index, IntLit):
        #     raise CompilerError(f"List assignment index must be an int literal: {base}[...]")
        # idx = la.index.value
        # if idx < 0 or idx >= n:
        #     raise CompilerError(f"Index out of bounds at compile time: {base}[{idx}], len={n}")

        # target = self.mangle_list_elem(base, idx)

        # # Compile RHS
        # if isinstance(la.expr, ExprA):
        #     self.CA(la.expr.aexp)
        # elif isinstance(la.expr, ExprB):
        #     if not isinstance(elem_t, TypeInt):
        #         raise CompilerError("Storing boolean into Float list element is not allowed.")
        #     self.CB(la.expr.bexp)
        # else:
        #     raise CompilerError("Unknown Expr variant.")

        # if not isinstance(elem_t, (TypeInt, TypeFloat)):
        #     raise CompilerError("Nested list elements not supported.")
        # self.emit("STORE", target)

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