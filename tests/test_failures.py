import tempfile
import unittest
from pathlib import Path

from app import build_service
from src.domain import Actor, Conflict, PermissionDenied, ValidationError


CREATE_DATA = {'taxpayer': 'Star Ltd', 'tax_period': '2025-Q4', 'declared_tax': 500000.0, 'assessed_tax': 760000.0, 'penalty_rate': 0.2, 'days_late': 90, 'appeal_deadline_day': 60}
EVIDENCE = {'evidences': [{'type': '书证', 'pages': 8}]}
FLOW = [
    ('investigate', 'inspector', {'plan': '核对账簿'}),
    ('register_evidence', 'inspector', EVIDENCE),
    ('propose', 'inspector', {'proposal': '补税并处罚'}),
    ('review', 'reviewer', {'outcome': 'accepted', 'review_note': '证据充分'}),
    ('close', 'reviewer', {'final_decision': '维持处理'}),
]
APPEAL_FLOW = [
    ('investigate', 'inspector', {'plan': '核对账簿'}),
    ('register_evidence', 'inspector', EVIDENCE),
    ('propose', 'inspector', {'proposal': '补税并处罚'}),
    ('review', 'reviewer', {'outcome': 'accepted', 'review_note': '证据充分'}),
    ('appeal', 'taxpayer_rep', {'appeal_day': 10, 'appeal_reason': '存在异议'}),
]


class FailureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_service(str(Path(self.temp.name) / "test.db"))

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, record, steps):
        for action, role, data in steps:
            record = self.service.act(Actor("operator", role), record["id"], record["version"], action, data)
        return record

    def test_permission_and_duplicate(self):
        with self.assertRaises(PermissionDenied):
            self.service.create(Actor("outsider", "outsider"), "TAX-26001", CREATE_DATA)
        self.service.create(Actor("creator", "inspector"), "TAX-26001", CREATE_DATA)
        with self.assertRaises(Conflict):
            self.service.create(Actor("creator", "inspector"), "TAX-26001", CREATE_DATA)

    def test_stale_version_is_rejected(self):
        record = self.service.create(Actor("creator", "inspector"), "TAX-26001", CREATE_DATA)
        record = self._run(record, FLOW[:1])
        with self.assertRaises(Conflict):
            self.service.act(Actor("operator", "inspector"), record["id"], record["version"] - 1, "register_evidence", EVIDENCE)

    def test_propose_requires_evidence_registry(self):
        record = self.service.create(Actor("creator", "inspector"), "TAX-26001", CREATE_DATA)
        record = self._run(record, FLOW[:1])
        with self.assertRaises(ValidationError):
            self.service.act(Actor("operator", "inspector"), record["id"], record["version"], "propose", {'proposal': '补税并处罚'})

    def test_evidence_type_must_be_allowed(self):
        record = self.service.create(Actor("creator", "inspector"), "TAX-26001", CREATE_DATA)
        record = self._run(record, FLOW[:1])
        with self.assertRaises(ValidationError):
            self.service.act(Actor("operator", "inspector"), record["id"], record["version"], "register_evidence", {'evidences': [{'type': '传闻', 'pages': 2}]})
        with self.assertRaises(ValidationError):
            self.service.act(Actor("operator", "inspector"), record["id"], record["version"], "register_evidence", {'evidences': [{'type': '书证', 'pages': 0}]})

    def test_appeal_supplements_and_close_locks(self):
        record = self.service.create(Actor("creator", "inspector"), "TAX-26001", CREATE_DATA)
        record = self._run(record, APPEAL_FLOW)
        # 复议期间纳税人可以补录新证据。
        record = self.service.act(
            Actor("tp", "taxpayer_rep"), record["id"], record["version"],
            "register_evidence", {'evidences': [{'type': '证人证言', 'pages': 2, 'title': '供应商说明'}]},
        )
        self.assertEqual(record["payload"]["evidence_count"], 2)
        self.assertEqual(record["payload"]["evidences"][-1]["source"], "纳税人补录")
        # 稽查人员无权在复议期间登记证据。
        with self.assertRaises(PermissionDenied):
            self.service.act(Actor("op", "inspector"), record["id"], record["version"], "register_evidence", EVIDENCE)
        record = self.service.act(Actor("rv", "reviewer"), record["id"], record["version"], "close", {'final_decision': '复议后结案'})
        due_before = record["payload"]["total_due"]
        # 结案后证据和金额全部锁定：补录、改金额、其他动作全部拒绝。
        with self.assertRaises(Conflict):
            self.service.act(Actor("tp", "taxpayer_rep"), record["id"], record["version"], "register_evidence", EVIDENCE)
        with self.assertRaises(Conflict):
            self.service.act(Actor("rv", "reviewer"), record["id"], record["version"], "review", {'outcome': 'reduced', 'review_note': '再减'})
        locked = self.service.get_record(Actor("rv", "reviewer"), record["id"])
        self.assertEqual(locked["payload"]["evidence_count"], 2)
        self.assertEqual(locked["payload"]["total_due"], due_before)
