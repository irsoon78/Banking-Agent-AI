"""独立 Risk Engine 测试，无 LLM、数据库或网络依赖。"""

import unittest
from decimal import Decimal

from security import RiskEngine


class RiskEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = RiskEngine()

    def test_balance_query_is_green(self):
        self.assertEqual(self.engine.evaluate("get_balance"), {
            "risk_level": "green", "reason": "查询类操作可自动执行",
        })

    def test_bill_and_query_operations_are_green(self):
        for operation in ("get_bills", "query", "query_balance", "query_bill", "query_bills"):
            with self.subTest(operation=operation):
                self.assertEqual(self.engine.evaluate(operation)["risk_level"], "green")

    def test_transfer_500_is_yellow(self):
        self.assertEqual(self.engine.evaluate("transfer", 500), {
            "risk_level": "yellow", "reason": "日累计转账不超过1000元，需要用户确认",
        })

    def test_transfer_5000_is_red(self):
        self.assertEqual(self.engine.evaluate("transfer", 5000), {
            "risk_level": "red", "reason": "日累计转账超过1000元，需要强验证",
        })

    def test_threshold_and_smallest_cent(self):
        for amount, level in (("0.01", "yellow"), (1000, "yellow"), ("1000.01", "red")):
            with self.subTest(amount=amount):
                self.assertEqual(self.engine.evaluate("transfer", amount)["risk_level"], level)

    def test_decimal_precision_is_not_rounded_down(self):
        self.assertEqual(self.engine.evaluate("transfer", Decimal("1000.0000000000000000001"))["risk_level"], "red")

    def test_numeric_formats_and_existing_tool_alias(self):
        for amount in (500, 500.0, "500.00", Decimal("500.00")):
            with self.subTest(amount=amount):
                self.assertEqual(self.engine.evaluate("transfer_money", amount)["risk_level"], "yellow")

    def test_missing_and_invalid_amounts_are_red(self):
        for amount in (None, "", "abc", True, False, 0, -1, "-0.01", float("nan"), float("inf"),
                       Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), [], {}):
            with self.subTest(amount=repr(amount)):
                result = self.engine.evaluate("transfer", amount)
                self.assertEqual(result["risk_level"], "red")
                self.assertTrue(result["reason"])

    def test_unknown_operations_are_red(self):
        # 不按名称前缀将任意未知操作视为低风险查询。
        for operation in ("delete_account", "query_delete_account", "transfer_all", ""):
            with self.subTest(operation=operation):
                self.assertEqual(self.engine.evaluate(operation)["risk_level"], "red")

    def test_invalid_operation_types_are_red(self):
        for operation in (None, True, 123, [], {}):
            with self.subTest(operation=repr(operation)):
                self.assertEqual(self.engine.evaluate(operation)["risk_level"], "red")

    def test_reserved_high_risk_operations_are_red(self):
        for operation in ("high_risk", "investment", "card_management", "subscription_management"):
            with self.subTest(operation=operation):
                self.assertEqual(self.engine.evaluate(operation, 500)["risk_level"], "red")

    def test_operation_normalization(self):
        self.assertEqual(self.engine.evaluate(" GET_BALANCE ")["risk_level"], "green")
        self.assertEqual(self.engine.evaluate(" Transfer ", 500)["risk_level"], "yellow")

    def test_output_contract(self):
        for operation, amount in (("get_balance", None), ("transfer", 500), ("transfer", 5000), ("unknown", None)):
            result = self.engine.evaluate(operation, amount)
            self.assertEqual(set(result), {"risk_level", "reason"})
            self.assertIn(result["risk_level"], {"green", "yellow", "red"})
            self.assertIsInstance(result["reason"], str)


if __name__ == "__main__":
    unittest.main()
