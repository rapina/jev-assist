"""Axis scoring, selection boundaries and provider validation."""
import copy
import json
import unittest
from routing_policy import (AXES, ASTRA, SOL, TERRA, LUNA, TIERS, EFFORTS, QUESTIONS,
                            decision_from_answers, route_choice, route)


class AxisPolicy(unittest.TestCase):
    def answer(self, model=LUNA, effort="medium", **scores):
        choices = route_choice(model, effort)
        choices.update({k: str(v) for k, v in scores.items()})
        return {k: {"choice": v, "confidence": 0.3} for k, v in choices.items()}

    def test_contract_is_compact(self):
        self.assertLessEqual(len(json.dumps(QUESTIONS, separators=(",", ":"))), 3200)
        self.assertEqual(set(QUESTIONS), {*AXES, "effort", "lease"})

    def test_selection_boundaries(self):
        cases = [({}, LUNA), ({"coding": 1}, TERRA), ({"coding": 2}, TERRA),
                 ({"architecture": 4}, SOL), ({"coding": 4}, SOL),
                 ({"visual": 1, "architecture": 4}, SOL),
                 ({"visual": 2, "architecture": 2}, ASTRA),
                 ({"visual": 3}, ASTRA), ({"risk": 3}, ASTRA),
                 ({"coding": "unknown"}, SOL),
                 ({"visual": 3, "architecture": "unknown"}, ASTRA)]
        for scores, model in cases:
            with self.subTest(scores=scores):
                decision = decision_from_answers(self.answer(**scores))
                self.assertEqual(decision["model"], model)
                self.assertEqual(decision["effort"], "medium")
                self.assertEqual(set(decision["dimensions"]), set(AXES))

    def test_fixture_routes_and_efforts(self):
        for model in TIERS:
            for effort in EFFORTS:
                self.assertEqual(decision_from_answers(self.answer(model, effort))["model"], model)
                self.assertEqual(route(model, effort, 0, {"errored": True}), (model, effort, "default", "apply"))

    def test_missing_and_invalid_axes_fail_closed(self):
        for name in QUESTIONS:
            answer = self.answer()
            del answer[name]
            with self.assertRaises(ValueError):
                decision_from_answers(answer)
            for bad in (None, 5, "5", [], True):
                answer = self.answer()
                answer[name]["choice"] = bad
                with self.assertRaises(ValueError):
                    decision_from_answers(answer)

    def test_probability_validation_and_diagnostic_confidence(self):
        for name, question in QUESTIONS.items():
            choices = question["criteria"]
            for bad in ({}, dict.fromkeys(choices, 0), {k: float("nan") for k in choices}):
                answer = self.answer()
                answer[name]["probabilities"] = bad
                with self.assertRaises(ValueError):
                    decision_from_answers(answer)
        answer = self.answer(TERRA)
        for v in answer.values():
            v['confidence'] = False
        self.assertIsNone(decision_from_answers(answer)['confidence'])


if __name__ == "__main__":
    unittest.main()
