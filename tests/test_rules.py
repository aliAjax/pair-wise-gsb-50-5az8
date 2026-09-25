import unittest

from src.domain import Actor, ValidationError
from src.rules import DomainRules


CREATE_DATA = {'taxpayer': 'Star Ltd', 'tax_period': '2025-Q4', 'declared_tax': 500000.0, 'assessed_tax': 760000.0, 'penalty_rate': 0.2, 'evidence_count': 2, 'days_late': 90, 'appeal_deadline_day': 60}
EVIDENCES = {'evidences': [
    {'type': 'documentary', 'title': '销售合同', 'pages': 12},
    {'type': 'electronic_data', 'pages': 5},
]}
FLOW = [('investigate', 'inspector', {'plan': '核对账簿'}, 'investigating'), ('add_evidence', 'inspector', EVIDENCES, 'investigating'), ('propose', 'inspector', {'proposal': '补税并处罚'}, 'proposed'), ('review', 'reviewer', {'outcome': 'accepted', 'review_note': '证据充分'}, 'reviewed'), ('close', 'reviewer', {'final_decision': '维持处理'}, 'closed')]


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.rules = DomainRules()

    def test_prepare_create(self):
        prepared = self.rules.prepare_create(CREATE_DATA)
        self.assertEqual(prepared["tax_difference"], 260000.0)
        self.assertEqual(prepared["interest"], 11700.0)
        self.assertEqual(prepared["total_due"], 323700.0)
        self.assertEqual(prepared["evidences"], [])

    def test_action_calculation(self):
        action, role, data, expected_state = FLOW[0]
        record = {"id": 1, "state": self.rules.INITIAL_STATE, "payload": self.rules.prepare_create(CREATE_DATA)}
        state, payload, summary = self.rules.apply_action(record, action, data, role)
        self.assertEqual(state, expected_state)
        self.assertEqual(payload["investigation_plan"], "核对账簿")

    def test_evidence_registration(self):
        record = {"id": 1, "state": "investigating", "payload": self.rules.prepare_create(CREATE_DATA)}
        action, role, data, _expected_state = FLOW[1]
        state, payload, _summary = self.rules.apply_action(record, action, data, role)
        self.assertEqual(state, "investigating")
        self.assertEqual([item["type"] for item in payload["evidences"]], ["documentary", "electronic_data"])
        self.assertEqual(payload["evidences"][1]["seq"], 2)
        self.assertEqual(payload["evidences"][1]["pages"], 5)

    def test_invalid_input(self):
        invalid = dict(CREATE_DATA)
        invalid["penalty_rate"] = 2.0
        with self.assertRaises(ValidationError):
            self.rules.prepare_create(invalid)

    def test_invalid_evidence_type(self):
        record = {"id": 1, "state": "investigating", "payload": self.rules.prepare_create(CREATE_DATA)}
        with self.assertRaises(ValidationError):
            self.rules.apply_action(record, "add_evidence", {'evidences': [{'type': '口供', 'pages': 3}]}, "inspector")

    def test_invalid_evidence_pages(self):
        record = {"id": 1, "state": "investigating", "payload": self.rules.prepare_create(CREATE_DATA)}
        with self.assertRaises(ValidationError):
            self.rules.apply_action(record, "add_evidence", {'evidences': [{'type': 'documentary', 'pages': 0}]}, "inspector")
