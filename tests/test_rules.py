import unittest

from src.domain import ValidationError
from src.rules import EVIDENCE_TYPES, DomainRules


CREATE_DATA = {'taxpayer': 'Star Ltd', 'tax_period': '2025-Q4', 'declared_tax': 500000.0, 'assessed_tax': 760000.0, 'penalty_rate': 0.2, 'days_late': 90, 'appeal_deadline_day': 60}
FLOW = [('investigate', 'inspector', {'plan': '核对账簿'}, 'investigating'), ('propose', 'inspector', {'proposal': '补税并处罚'}, 'proposed'), ('review', 'reviewer', {'outcome': 'accepted', 'review_note': '证据充分'}, 'reviewed'), ('close', 'reviewer', {'final_decision': '维持处理'}, 'closed')]


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.rules = DomainRules()

    def test_prepare_create(self):
        data = dict(CREATE_DATA)
        data['evidences'] = [{'type': '书证', 'pages': 10}, {'type': '鉴定意见', 'pages': 4}]
        prepared = self.rules.prepare_create(data)
        self.assertEqual(prepared["tax_difference"], 260000.0)
        self.assertEqual(prepared["interest"], 11700.0)
        self.assertEqual(prepared["total_due"], 323700.0)
        self.assertEqual(prepared["evidence_count"], 2)
        self.assertEqual(prepared["total_evidence_pages"], 14)
        self.assertEqual(prepared["evidences"][0]["seq"], 1)
        self.assertEqual(prepared["evidences"][0]["source"], "稽查登记")

    def test_create_without_evidence_list(self):
        prepared = self.rules.prepare_create(CREATE_DATA)
        self.assertEqual(prepared["evidence_count"], 0)
        self.assertEqual(prepared["evidences"], [])

    def test_action_calculation(self):
        action, role, data, expected_state = FLOW[0]
        record = {"id": 1, "state": self.rules.INITIAL_STATE, "payload": self.rules.prepare_create(CREATE_DATA)}
        state, payload, summary = self.rules.apply_action(record, action, data)
        self.assertEqual(state, expected_state)
        self.assertEqual(payload["investigation_plan"], "核对账簿")

    def test_register_evidence_appends_and_keeps_state(self):
        record = {"id": 1, "state": "investigating", "payload": self.rules.prepare_create(CREATE_DATA)}
        state, payload, summary = self.rules.apply_action(
            record, "register_evidence", {'evidences': [{'type': '电子数据', 'pages': 5}, {'type': '物证', 'pages': 2}]}
        )
        self.assertEqual(state, "investigating")
        self.assertEqual(payload["evidence_count"], 2)
        self.assertEqual(payload["total_evidence_pages"], 7)
        self.assertEqual([item["seq"] for item in payload["evidences"]], [1, 2])
        self.assertIn("证据2份", summary)

    def test_invalid_input(self):
        invalid = dict(CREATE_DATA)
        invalid["penalty_rate"] = 2.0
        with self.assertRaises(ValidationError):
            self.rules.prepare_create(invalid)

    def test_evidence_validation(self):
        with self.assertRaises(ValidationError):
            self.rules.validate_evidences([{'type': '不在范围内', 'pages': 1}])
        with self.assertRaises(ValidationError):
            self.rules.validate_evidences([{'type': EVIDENCE_TYPES[0], 'pages': -1}])
        with self.assertRaises(ValidationError):
            self.rules.validate_evidences("不是列表")
