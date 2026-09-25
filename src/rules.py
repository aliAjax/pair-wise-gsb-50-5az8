"""税务稽查案件与复议流程领域规则与状态转换。"""
from typing import Any, Dict, Iterable, List, Tuple

from .domain import Actor, Conflict, PermissionDenied, ValidationError, boolean, choice, integer, number, text, text_list


INITIAL_STATE = "opened"
LOCKED_STATE = "closed"
CREATE_ROLES = {'inspector'}
ACTION_ROLES = {'investigate': {'inspector'}, 'add_evidence': {'inspector', 'taxpayer_rep'}, 'propose': {'inspector'}, 'review': {'reviewer'}, 'appeal': {'taxpayer_rep'}, 'close': {'reviewer'}}
TRANSITIONS = {'investigate': {'opened': 'investigating'}, 'add_evidence': {'investigating': 'investigating', 'appealed': 'appealed'}, 'propose': {'investigating': 'proposed'}, 'review': {'proposed': 'reviewed'}, 'appeal': {'reviewed': 'appealed'}, 'close': {'reviewed': 'closed', 'appealed': 'closed'}}

# 证据类型只能在以下规定范围内登记
EVIDENCE_TYPES = (
    ("documentary", "书证"),
    ("physical", "物证"),
    ("audio_visual", "视听资料"),
    ("electronic_data", "电子数据"),
    ("witness_testimony", "证人证言"),
    ("party_statement", "当事人陈述"),
    ("expert_opinion", "鉴定意见"),
    ("inquest_record", "勘验笔录、现场笔录"),
)
EVIDENCE_TYPE_CODES = [code for code, _label in EVIDENCE_TYPES]
# 同一动作在不同阶段允许的角色：调查阶段由稽查人员登记，复议阶段由纳税人补录
STATE_ACTION_ROLES = {'add_evidence': {'investigating': {'inspector'}, 'appealed': {'taxpayer_rep'}}}


class DomainRules:
    INITIAL_STATE = INITIAL_STATE

    def known_role(self, role: str) -> bool:
        all_roles = set(CREATE_ROLES)
        for roles in ACTION_ROLES.values():
            all_roles.update(roles)
        return role == "admin" or role in all_roles

    def role_can_create(self, role: str) -> bool:
        return role == "admin" or role in CREATE_ROLES

    def role_can_action(self, role: str, action: str) -> bool:
        return role == "admin" or role in ACTION_ROLES.get(action, set())

    def role_can_action_in_state(self, role: str, action: str, state: str) -> bool:
        if role == "admin":
            return True
        allowed = STATE_ACTION_ROLES.get(action)
        if allowed is None:
            return role in ACTION_ROLES.get(action, set())
        return role in allowed.get(state, set())

    @staticmethod
    def evidence_catalog() -> List[Dict[str, str]]:
        return [{"code": code, "label": label} for code, label in EVIDENCE_TYPES]

    @staticmethod
    def validate_evidence_item(raw: Any, index: int) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValidationError("第%s份证据必须是对象" % index)
        item: Dict[str, Any] = {
            "type": choice(raw, "type", EVIDENCE_TYPE_CODES),
            "pages": integer(raw, "pages", 1),
        }
        title = raw.get("title")
        if title is not None:
            item["title"] = text(raw, "title")
        return item

    def validate_evidences(self, value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list) or not value:
            raise ValidationError("evidences必须是非空列表，每份证据需登记类型和页数")
        items: List[Dict[str, Any]] = []
        for index, raw in enumerate(value, start=1):
            items.append(self.validate_evidence_item(raw, index))
        return items

    def validate_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        p = dict(payload)
        text(p, "taxpayer")
        text(p, "tax_period")
        number(p, "declared_tax", 0)
        number(p, "assessed_tax", 0)
        number(p, "penalty_rate", 0, 1)
        integer(p, "evidence_count", 0)
        integer(p, "days_late", 0)
        integer(p, "appeal_deadline_day", 1)
        p["evidences"] = []
        return p

    def prepare_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        p = self.validate_create(payload)
        difference = max(0.0, float(p["assessed_tax"]) - float(p["declared_tax"]))
        interest = difference * 0.0005 * int(p["days_late"])
        penalty = difference * float(p["penalty_rate"])
        p["tax_difference"] = round(difference, 2)
        p["interest"] = round(interest, 2)
        p["penalty"] = round(penalty, 2)
        p["total_due"] = round(difference + interest + penalty, 2)
        p["refund_due"] = round(max(0.0, float(p["declared_tax"]) - float(p["assessed_tax"])), 2)
        return p

    def check_create_conflicts(self, payload: Dict[str, Any], existing: Iterable[Dict[str, Any]]) -> None:
        for item in existing:
            if item["state"] not in {"closed"} and item["payload"].get("taxpayer") == payload.get("taxpayer") and item["payload"].get("tax_period") == payload.get("tax_period"):
                raise Conflict("同一纳税人同一税期已有未结稽查案件")

    def require_transition(self, record: Dict[str, Any], action: str) -> str:
        if record["state"] == LOCKED_STATE:
            raise Conflict("案件已结案，证据和金额已锁定，不能再补录或改动")
        allowed = TRANSITIONS.get(action, {}).get(record["state"])
        if allowed is None:
            raise Conflict("当前状态不允许执行%s" % action)
        return allowed

    def apply_action(self, record: Dict[str, Any], action: str, data: Dict[str, Any], actor_role: str = "") -> Tuple[str, Dict[str, Any], str]:
        new_state = self.require_transition(record, action)
        if actor_role and not self.role_can_action_in_state(actor_role, action, record["state"]):
            raise PermissionDenied("当前阶段角色无权执行该操作")
        data = dict(data or {})
        p = dict(record["payload"])
        changes: Dict[str, Any] = {}
        summary = ""
        if action == "investigate":
            changes["investigation_plan"] = text(data, "plan")
            summary = "进入稽查调查"
        elif action == "add_evidence":
            items = self.validate_evidences(data.get("evidences"))
            evidences = list(p.get("evidences") or [])
            stage = "appeal" if record["state"] == "appealed" else "investigation"
            for item in items:
                evidences.append({
                    "seq": len(evidences) + 1,
                    "type": item["type"],
                    "pages": item["pages"],
                    "title": item.get("title", ""),
                    "stage": stage,
                })
            changes["evidences"] = evidences
            summary = "%s阶段补录%s份证据" % ("复议" if stage == "appeal" else "调查", len(items))
        elif action == "propose":
            evidences = p.get("evidences") or []
            if not evidences:
                raise ValidationError("证据未登记，不能提出处理建议")
            required = int(p["evidence_count"])
            if len(evidences) != required:
                raise ValidationError("证据未登记完整：应登记%s份，实际%s份" % (required, len(evidences)))
            changes["proposal"] = text(data, "proposal")
            changes["proposed_amount"] = float(p["total_due"])
            summary = "已提出补税和处罚建议"
        elif action == "review":
            outcome = choice(data, "outcome", ["accepted", "reduced", "remanded"])
            changes["review_outcome"] = outcome
            changes["review_note"] = text(data, "review_note")
            if outcome == "reduced":
                changes["total_due"] = round(float(p["total_due"]) * float(data.get("reduction_pct", 0.5)), 2)
            summary = "复核完成"
        elif action == "appeal":
            appeal_day = integer(data, "appeal_day", 0)
            if appeal_day > int(p["appeal_deadline_day"]):
                raise ValidationError("复议申请超过期限")
            changes["appeal_day"] = appeal_day
            changes["appeal_reason"] = text(data, "appeal_reason")
            summary = "复议申请已受理"
        elif action == "close":
            changes["final_decision"] = text(data, "final_decision")
            summary = "案件已结案"
        p.update(changes)
        return new_state, p, summary or ("已执行%s" % action)
