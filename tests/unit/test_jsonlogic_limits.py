import pytest
from apps.workflows.evaluator import (
    MAX_AST_NODES,
    MAX_RECURSION_DEPTH,
    RuleEvaluationError,
    SafeJSONLogicEvaluator,
)


class TestSafeJSONLogicEvaluatorLimits:
    def test_valid_rule_evaluates_successfully(self):
        context = {"data": {"amount": 750, "status": "pending"}}
        rule = {
            "and": [
                {"<=": [{"var": "data.amount"}, 1000]},
                {"==": [{"var": "data.status"}, "pending"]},
            ]
        }
        assert SafeJSONLogicEvaluator.evaluate(rule, context) is True

        context_failing = {"data": {"amount": 1500, "status": "pending"}}
        assert SafeJSONLogicEvaluator.evaluate(rule, context_failing) is False

    def test_recursion_depth_limit_exceeded_raises_error(self):
        # Construct deeply nested rule exceeding MAX_RECURSION_DEPTH (10)
        deep_rule = {"==": [1, 1]}
        for _ in range(MAX_RECURSION_DEPTH + 2):
            deep_rule = {"and": [deep_rule, {"==": [1, 1]}]}

        with pytest.raises(RuleEvaluationError, match="recursion depth"):
            SafeJSONLogicEvaluator.evaluate(deep_rule, {})

    def test_node_count_limit_exceeded_raises_error(self):
        # Construct wide rule with more than MAX_AST_NODES (50) nodes at shallow depth
        nodes = [{"==": [i, i]} for i in range(MAX_AST_NODES + 10)]
        wide_rule = {"and": nodes}

        with pytest.raises(RuleEvaluationError, match="node count"):
            SafeJSONLogicEvaluator.evaluate(wide_rule, {})

    def test_disallowed_operator_rejected(self):
        rule = {"__import__": ["os"]}
        with pytest.raises(RuleEvaluationError, match="Unsupported or forbidden"):
            SafeJSONLogicEvaluator.evaluate(rule, {})

        eval_rule = {"eval": ["1+1"]}
        with pytest.raises(RuleEvaluationError, match="Unsupported or forbidden"):
            SafeJSONLogicEvaluator.evaluate(eval_rule, {})
