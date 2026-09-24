"""Bounded integer expression programs for address and issue policies.

No Python eval, exec, import, calls, attributes, indexing, or file access is used.
The design agent submits these programs as JSON. The evaluator owns the trace.
"""
from __future__ import annotations

import ast
import operator

NAMES = frozenset({"v", "b", "linear", "channels", "banks", "row_bursts",
                   "bursts_per_vector", "channel", "bank", "row", "column", "hit", "ordinal"})
BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
          ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
          ast.BitXor: operator.xor, ast.BitAnd: operator.and_, ast.BitOr: operator.or_,
          ast.LShift: operator.lshift, ast.RShift: operator.rshift}
COMPARE = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
           ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}


class ProgramError(ValueError):
    pass


class Expression:
    def __init__(self, source: str):
        if not isinstance(source, str) or not 0 < len(source) <= 512:
            raise ProgramError("expression must contain 1..512 characters")
        try:
            self.tree = ast.parse(source, mode="eval").body
        except (SyntaxError, RecursionError) as exc:
            raise ProgramError("invalid expression syntax") from exc
        self.source = source
        if sum(1 for _ in ast.walk(self.tree)) > 96:
            raise ProgramError("expression exceeds 96 AST nodes")
        self._validate(self.tree, 0)

    def _validate(self, node, depth):
        if depth > 16:
            raise ProgramError("expression exceeds depth limit")
        if isinstance(node, ast.Constant) and type(node.value) is int:
            if abs(node.value) > 2**31:
                raise ProgramError("constant is too large")
        elif isinstance(node, ast.Name) and node.id in NAMES:
            return
        elif isinstance(node, ast.BinOp) and type(node.op) in BINOPS:
            self._validate(node.left, depth + 1)
            self._validate(node.right, depth + 1)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Invert)):
            self._validate(node.operand, depth + 1)
        elif isinstance(node, ast.IfExp):
            for child in (node.test, node.body, node.orelse):
                self._validate(child, depth + 1)
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in COMPARE:
            self._validate(node.left, depth + 1)
            self._validate(node.comparators[0], depth + 1)
        else:
            raise ProgramError(f"unsupported expression node: {type(node).__name__}")

    def __call__(self, context: dict[str, int]) -> int:
        def visit(node):
            if isinstance(node, ast.Constant):
                value = node.value
            elif isinstance(node, ast.Name):
                if node.id not in context:
                    raise ProgramError(f"name unavailable in this stage: {node.id}")
                value = context[node.id]
            elif isinstance(node, ast.BinOp):
                a, b = visit(node.left), visit(node.right)
                if isinstance(node.op, (ast.LShift, ast.RShift)) and not 0 <= b <= 63:
                    raise ProgramError("shift outside 0..63")
                try:
                    value = BINOPS[type(node.op)](a, b)
                except (ZeroDivisionError, OverflowError) as exc:
                    raise ProgramError("undefined arithmetic") from exc
            elif isinstance(node, ast.UnaryOp):
                a = visit(node.operand)
                value = -a if isinstance(node.op, ast.USub) else ~a if isinstance(node.op, ast.Invert) else a
            elif isinstance(node, ast.IfExp):
                value = visit(node.body if visit(node.test) else node.orelse)
            elif isinstance(node, ast.Compare):
                value = int(COMPARE[type(node.ops[0])](visit(node.left), visit(node.comparators[0])))
            else:
                raise ProgramError("unsupported program")
            if abs(value) >= 2**63:
                raise ProgramError("integer exceeds signed 63-bit magnitude")
            return value
        return visit(self.tree)


def compile_program(program: dict | None) -> dict[str, Expression]:
    if program is None:
        return {}
    if not isinstance(program, dict) or not set(program) <= {"channel", "bank", "row", "column", "priority"}:
        raise ProgramError("program fields must be channel, bank, row, column, priority")
    address_fields = {"channel", "bank", "row", "column"}
    if set(program) & address_fields and not address_fields <= set(program):
        raise ProgramError("address program must define all four address coordinates")
    return {name: Expression(value) for name, value in program.items()}
