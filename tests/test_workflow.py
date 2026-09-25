import tempfile
import unittest
from pathlib import Path

from app import build_service
from src.domain import Actor, Conflict


CREATE_DATA = {'taxpayer': 'Star Ltd', 'tax_period': '2025-Q4', 'declared_tax': 500000.0, 'assessed_tax': 760000.0, 'penalty_rate': 0.2, 'days_late': 90, 'appeal_deadline_day': 60}
FLOW = [
    ('investigate', 'inspector', {'plan': '核对账簿'}, 'investigating'),
    ('register_evidence', 'inspector', {'evidences': [{'type': '书证', 'pages': 12}, {'type': '电子数据', 'pages': 3}]}, 'investigating'),
    ('propose', 'inspector', {'proposal': '补税并处罚'}, 'proposed'),
    ('review', 'reviewer', {'outcome': 'accepted', 'review_note': '证据充分'}, 'reviewed'),
    ('close', 'reviewer', {'final_decision': '维持处理'}, 'closed'),
]


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_service(str(Path(self.temp.name) / "test.db"))

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_workflow_and_audit(self):
        record = self.service.create(Actor("creator", "inspector"), "TAX-26001", CREATE_DATA)
        self.assertEqual(record["state"], "opened")
        for action, role, data, expected_state in FLOW:
            record = self.service.act(Actor("operator", role), record["id"], record["version"], action, data)
            self.assertEqual(record["state"], expected_state)
        payload = record["payload"]
        self.assertEqual(payload["evidence_count"], 2)
        self.assertEqual(payload["total_evidence_pages"], 15)
        self.assertEqual([item["seq"] for item in payload["evidences"]], [1, 2])
        self.assertTrue(payload["locked"])
        timeline = self.service.timeline(Actor("creator", "inspector"), record["id"])
        self.assertEqual(len(timeline), len(FLOW) + 1)
        self.assertEqual(timeline[-1]["action"], FLOW[-1][0])
