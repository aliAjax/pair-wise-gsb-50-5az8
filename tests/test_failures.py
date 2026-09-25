import tempfile
import unittest
from pathlib import Path

from app import build_service
from src.domain import Actor, Conflict, PermissionDenied, ValidationError


CREATE_DATA = {'taxpayer': 'Star Ltd', 'tax_period': '2025-Q4', 'declared_tax': 500000.0, 'assessed_tax': 760000.0, 'penalty_rate': 0.2, 'evidence_count': 2, 'days_late': 90, 'appeal_deadline_day': 60}
EVIDENCES = {'evidences': [
    {'type': 'documentary', 'title': '销售合同', 'pages': 12},
    {'type': 'electronic_data', 'pages': 5},
]}
FLOW = [('investigate', 'inspector', {'plan': '核对账簿'}, 'investigating'), ('add_evidence', 'inspector', EVIDENCES, 'investigating'), ('propose', 'inspector', {'proposal': '补税并处罚'}, 'proposed'), ('review', 'reviewer', {'outcome': 'accepted', 'review_note': '证据充分'}, 'reviewed'), ('close', 'reviewer', {'final_decision': '维持处理'}, 'closed')]


class FailureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_service(str(Path(self.temp.name) / "test.db"))

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, record, index):
        action, role, data, expected_state = FLOW[index]
        return self.service.act(Actor("operator", role), record["id"], record["version"], action, data), expected_state

    def test_permission_and_duplicate(self):
        with self.assertRaises(PermissionDenied):
            self.service.create(Actor("outsider", "outsider"), "TAX-26001", CREATE_DATA)
        self.service.create(Actor("creator", "inspector"), "TAX-26001", CREATE_DATA)
        with self.assertRaises(Conflict):
            self.service.create(Actor("creator", "inspector"), "TAX-26001", CREATE_DATA)

    def test_stale_version_is_rejected(self):
        record = self.service.create(Actor("creator", "inspector"), "TAX-26001", CREATE_DATA)
        record, _ = self._run(record, 0)
        with self.assertRaises(Conflict):
            self.service.act(Actor("operator", FLOW[1][1]), record["id"], record["version"] - 1, FLOW[1][0], FLOW[1][2])

    def test_propose_requires_complete_evidence(self):
        record = self.service.create(Actor("creator", "inspector"), "TAX-26002", CREATE_DATA)
        record, _ = self._run(record, 0)
        # 一份证据都没登记，不能提出处理建议
        with self.assertRaises(ValidationError):
            self.service.act(Actor("operator", "inspector"), record["id"], record["version"], "propose", {'proposal': '补税'})
        # 只登记1份，但应登记2份：仍不完整
        record = self.service.act(Actor("operator", "inspector"), record["id"], record["version"], "add_evidence",
                                  {'evidences': [{'type': 'documentary', 'pages': 10}]})
        with self.assertRaises(ValidationError):
            self.service.act(Actor("operator", "inspector"), record["id"], record["version"], "propose", {'proposal': '补税'})

    def test_evidence_type_out_of_range(self):
        record = self.service.create(Actor("creator", "inspector"), "TAX-26003", CREATE_DATA)
        record, _ = self._run(record, 0)
        with self.assertRaises(ValidationError):
            self.service.act(Actor("operator", "inspector"), record["id"], record["version"], "add_evidence",
                             {'evidences': [{'type': '口供笔录', 'pages': 3}]})

    def test_taxpayer_can_add_evidence_only_in_appeal(self):
        record = self.service.create(Actor("creator", "inspector"), "TAX-26004", CREATE_DATA)
        record, _ = self._run(record, 0)
        # 调查阶段纳税人代表不能登记证据
        with self.assertRaises(PermissionDenied):
            self.service.act(Actor("taxpayer", "taxpayer_rep"), record["id"], record["version"], "add_evidence",
                             {'evidences': [{'type': 'witness_testimony', 'pages': 2}]})
        # 走完整流程并进入复议
        for index in range(1, 4):
            record, _ = self._run(record, index)
        record = self.service.act(Actor("tp", "taxpayer_rep"), record["id"], record["version"], "appeal",
                                  {'appeal_day': 10, 'appeal_reason': '补税金额有误'})
        self.assertEqual(record["state"], "appealed")
        # 复议期间纳税人可以补录新证据
        record = self.service.act(Actor("tp", "taxpayer_rep"), record["id"], record["version"], "add_evidence",
                                  {'evidences': [{'type': 'documentary', 'title': '补充合同附件', 'pages': 8}]})
        self.assertEqual(len(record["payload"]["evidences"]), 3)
        self.assertEqual(record["payload"]["evidences"][-1]["stage"], "appeal")

    def test_closed_case_locks_evidence_and_amount(self):
        record = self.service.create(Actor("creator", "inspector"), "TAX-26005", CREATE_DATA)
        for index in range(len(FLOW)):
            record, _ = self._run(record, index)
        self.assertEqual(record["state"], "closed")
        locked_amount = record["payload"]["total_due"]
        with self.assertRaises(Conflict):
            self.service.act(Actor("operator", "inspector"), record["id"], record["version"], "add_evidence",
                             {'evidences': [{'type': 'documentary', 'pages': 1}]})
        with self.assertRaises(Conflict):
            self.service.act(Actor("operator", "reviewer"), record["id"], record["version"], "review",
                             {'outcome': 'reduced', 'review_note': '结案后改金额', 'reduction_pct': 0.5})
        record = self.service.get_record(Actor("creator", "inspector"), record["id"])
        self.assertEqual(record["payload"]["total_due"], locked_amount)
        self.assertEqual(len(record["payload"]["evidences"]), 2)
