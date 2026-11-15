from typing import Any

MAX_RECURSION_DEPTH = 10
MAX_AST_NODES = 50


class RuleEvaluationError(Exception):
    """Raised when rule structure is invalid or exceeds evaluation safety bounds."""

    pass


class SafeJSONLogicEvaluator:
    """Safe, deterministic Abstract Syntax Tree (AST) tree-walking interpreter.

    Guarantees:
    1. Zero usage of Python's eval() or exec().
    2. Strict whitelist of operators: ==, !=, >, <, >=, <=, and, or, not, in, var.
    3. Maximum recursion depth limit (10) and AST node count limit (50) to prevent DoS.
    4. Safe dot-notation variable resolution against context dictionary.
    """

    ALLOWED_OPERATORS = {
        "==",
        "!=",
        ">",
        "<",
        ">=",
        "<=",
        "and",
        "or",
        "not",
        "in",
        "var",
    }

    def __init__(self, rule: Any, context: dict[str, Any]):
        self.rule = rule
        self.context = context or {}
        self.node_count = 0

    @classmethod
    def evaluate(cls, rule: Any, context: dict[str, Any]) -> bool:
        if rule is None:
            return True
        evaluator = cls(rule, context)
        return bool(evaluator._walk(rule, depth=0))

    def _resolve_var(self, path: Any) -> Any:
        if not path or not isinstance(path, str):
            return None
        parts = path.split(".")
        current = self.context
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _walk(self, node: Any, depth: int) -> Any:
        self.node_count += 1
        if self.node_count > MAX_AST_NODES:
            raise RuleEvaluationError(
                f"Rule node count exceeded safety limit of {MAX_AST_NODES} nodes"
            )
        if depth > MAX_RECURSION_DEPTH:
            raise RuleEvaluationError(
                f"Rule recursion depth exceeded safety limit of {MAX_RECURSION_DEPTH}"
            )

        # Literal values (primitives)
        if not isinstance(node, dict):
            return node

        if len(node) != 1:
            raise RuleEvaluationError(
                f"Invalid JSONLogic node: expected single operator key, found {list(node.keys())}"
            )

        operator, args = next(iter(node.items()))
        if operator not in self.ALLOWED_OPERATORS:
            raise RuleEvaluationError(f"Unsupported or forbidden operator: '{operator}'")

        # 1. Variable lookup: {"var": "path.to.val"}
        if operator == "var":
            if isinstance(args, list) and args:
                return self._resolve_var(args[0])
            return self._resolve_var(args)

        # Ensure args is a list for multi-operand operators
        if not isinstance(args, list):
            operands = [args]
        else:
            operands = args

        # 2. Logic Operators
        if operator == "and":
            for op in operands:
                if not bool(self._walk(op, depth + 1)):
                    return False
            return True

        elif operator == "or":
            for op in operands:
                if bool(self._walk(op, depth + 1)):
                    return True
            return False

        elif operator == "not":
            if not operands:
                return True
            return not bool(self._walk(operands[0], depth + 1))

        # 3. Comparison & Membership operators (require evaluated operands)
        evaluated = [self._walk(op, depth + 1) for op in operands]
        if len(evaluated) < 2:
            raise RuleEvaluationError(f"Operator '{operator}' requires at least 2 operands")

        left, right = evaluated[0], evaluated[1]

        if operator == "==":
            return left == right
        elif operator == "!=":
            return left != right
        elif operator == ">":
            return left is not None and right is not None and left > right
        elif operator == "<":
            return left is not None and right is not None and left < right
        elif operator == ">=":
            return left is not None and right is not None and left >= right
        elif operator == "<=":
            return left is not None and right is not None and left <= right
        elif operator == "in":
            # {"in": ["Finance", {"var": "user.roles"}]}
            if right is None:
                return False
            if isinstance(right, (list, tuple, set, str)):
                return left in right
            return False

        return False
