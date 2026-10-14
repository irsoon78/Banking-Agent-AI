"""权限策略和 Risk Engine 组合测试，无 LLM、数据库或网络依赖。"""

import unittest

from security import PermissionController, RiskEngine


class PermissionControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = PermissionController()
        self.risk_engine = RiskEngine()

    def decision_for(self, operation, amount=None):
        risk = self.risk_engine.evaluate(operation, amount)
        return self.controller.decide(operation, risk["risk_level"])

    def test_green_balance_query_is_allowed(self):
        self.assertEqual(self.decision_for("get_balance"), {
            "action": "allow", "require_confirmation": False, "require_verification": False,
        })

    def test_yellow_transfer_500_requires_confirmation(self):
        self.assertEqual(self.decision_for("transfer", 500), {
            "action": "pending_confirmation", "require_confirmation": True, "require_verification": False,
        })

    def test_red_transfer_5000_requires_verification(self):
        self.assertEqual(self.decision_for("transfer", 5000), {
            "action": "require_verification", "require_confirmation": False, "require_verification": True,
        })

    def test_bill_query_is_allowed(self):
        self.assertEqual(self.decision_for("get_bills")["action"], "allow")

    def test_transfer_boundary_and_tool_alias(self):
        self.assertEqual(self.decision_for("transfer_money", "1000.00")["action"], "pending_confirmation")
        self.assertEqual(self.decision_for("transfer_money", "1000.01")["action"], "require_verification")

    def test_yellow_and_red_never_allow_direct_execution(self):
        for operation in ("get_balance", "transfer", "investment", "card_report_loss"):
            for risk_level in ("yellow", "red"):
                with self.subTest(operation=operation, risk_level=risk_level):
                    decision = self.controller.decide(operation, risk_level)
                    self.assertNotEqual(decision["action"], "allow")
                    self.assertTrue(decision["require_confirmation"] or decision["require_verification"])

    def test_invalid_risk_levels_require_verification(self):
        for level in ("", "unknown", "allow", None, True, False, 1, [], {}):
            with self.subTest(level=repr(level)):
                self.assertEqual(self.controller.decide("get_balance", level), {
                    "action": "require_verification", "require_confirmation": False, "require_verification": True,
                })

    def test_invalid_operations_do_not_allow_execution(self):
        for operation in ("", " ", None, True, 123, [], {}):
            with self.subTest(operation=repr(operation)):
                self.assertEqual(self.controller.decide(operation, "green")["action"], "require_verification")

    def test_unknown_operation_and_invalid_amount_follow_risk_engine(self):
        for operation, amount in (("unknown", None), ("transfer", None), ("transfer", -1), ("transfer", "NaN")):
            with self.subTest(operation=operation, amount=amount):
                self.assertEqual(self.decision_for(operation, amount)["action"], "require_verification")

    def test_reserved_high_risk_operations_require_verification(self):
        for operation in ("high_risk", "investment", "card_management", "subscription_management"):
            with self.subTest(operation=operation):
                self.assertEqual(self.decision_for(operation)["action"], "require_verification")

    def test_risk_level_normalization(self):
        self.assertEqual(self.controller.decide("get_balance", " GREEN ")["action"], "allow")
        self.assertEqual(self.controller.decide("transfer", " Yellow ")["action"], "pending_confirmation")
        self.assertEqual(self.controller.decide("transfer", " RED ")["action"], "require_verification")

    def test_output_contract(self):
        for level in ("green", "yellow", "red"):
            decision = self.controller.decide("get_balance", level)
            self.assertEqual(set(decision), {"action", "require_confirmation", "require_verification"})
            self.assertIs(type(decision["require_confirmation"]), bool)
            self.assertIs(type(decision["require_verification"]), bool)

    def test_mutating_one_result_does_not_change_policy(self):
        decision = self.controller.decide("transfer", "red")
        decision["action"] = "allow"
        decision["require_verification"] = False
        self.assertEqual(self.controller.decide("transfer", "red")["action"], "require_verification")
        self.assertTrue(self.controller.decide("transfer", "red")["require_verification"])


if __name__ == "__main__":
    unittest.main()
