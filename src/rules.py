"""税务稽查案件与复议流程领域规则与状态转换。"""
from typing import Any, Dict, Iterable, List, Tuple

from .domain import Actor, Conflict, ValidationError, choice, integer, number, optional_text, text


INITIAL_STATE = "opened"
LOCKED_STATE = "closed"
CREATE_ROLES = {'inspector'}
ACTION_ROLES = {'investigate': {'inspector'}, 'propose': {'inspector'}, 'review': {'reviewer'}, 'appeal': {'taxpayer_rep'}, 'close': {'reviewer'}}
TRANSITIONS = {'investigate': {'opened': 'investigating'}, 'propose': {'investigating': 'proposed'}, 'review': {'proposed': 'reviewed'}, 'appeal': {'reviewed': 'appealed'}, 'close': {'reviewed': 'closed', 'appealed': 'closed'}}

# 证据类型限定在《行政处罚法》规定的法定证据种类范围内。
EVIDENCE_TYPES = [
    "书证",
    "物证",
    "视听资料",
    "电子数据",
    "证人证言",
    "当事人陈述",
    "鉴定意见",
    "勘验现场笔录",
]

# 证据登记（register_evidence）不改变案件状态，按状态区分登记角色：
# 立案后由稽查人员登记；复议期间由纳税人补录。
EVIDENCE_REGISTER_ROLES = {
    'opened': {'inspector'},
    'investigating': {'inspector'},
    'appealed': {'taxpayer_rep'},
}
EVIDENCE_SOURCE_BY_STATE = {
    'opened': '稽查登记',
    'investigating': '稽查登记',
    'appealed': '纳税人补录',
}


def _validate_evidence_item(raw: Any, index: int) -> Dict[str, Any]:
    """校验单份证据：类型必须在规定范围内，页数必须为正整数。"""
    if not isinstance(raw, dict):
        raise ValidationError("第%s份证据必须是对象" % index)
    item: Dict[str, Any] = {
        "type": choice(raw, "type", EVIDENCE_TYPES),
        "pages": integer(raw, "pages", 1),
    }
    title = optional_text(raw, "title", "")
    if title:
        item["title"] = title
    return item


class DomainRules:
    INITIAL_STATE = INITIAL_STATE
    LOCKED_STATE = LOCKED_STATE
    EVIDENCE_TYPES = EVIDENCE_TYPES

    def known_role(self, role: str) -> bool:
        all_roles = set(CREATE_ROLES)
        for roles in ACTION_ROLES.values():
            all_roles.update(roles)
        for roles in EVIDENCE_REGISTER_ROLES.values():
            all_roles.update(roles)
        return role == "admin" or role in all_roles

    def role_can_create(self, role: str) -> bool:
        return role == "admin" or role in CREATE_ROLES

    def role_can_action(self, role: str, action: str, state: str = None) -> bool:
        if role == "admin":
            return True
        if action == "register_evidence":
            return role in EVIDENCE_REGISTER_ROLES.get(state, set())
        return role in ACTION_ROLES.get(action, set())

    def validate_evidences(self, data: Any, minimum: int = 1) -> List[Dict[str, Any]]:
        if not isinstance(data, list):
            raise ValidationError("evidences必须是证据清单（列表）")
        if len(data) < minimum:
            raise ValidationError("证据清单至少需要%s份证据" % minimum)
        return [_validate_evidence_item(item, index + 1) for index, item in enumerate(data)]

    @staticmethod
    def stamp_evidences(evidences: List[Dict[str, Any]], source: str, start_seq: int = 1) -> List[Dict[str, Any]]:
        stamped = []
        for offset, item in enumerate(evidences):
            entry = dict(item)
            entry["seq"] = start_seq + offset
            entry["source"] = source
            stamped.append(entry)
        return stamped

    @staticmethod
    def evidence_totals(evidences: List[Dict[str, Any]]) -> Tuple[int, int]:
        total_pages = sum(int(item["pages"]) for item in evidences)
        return len(evidences), total_pages

    def validate_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        p = dict(payload)
        text(p, "taxpayer")
        text(p, "tax_period")
        number(p, "declared_tax", 0)
        number(p, "assessed_tax", 0)
        number(p, "penalty_rate", 0, 1)
        integer(p, "days_late", 0)
        integer(p, "appeal_deadline_day", 1)
        # 证据清单替代单一的证据数量；创建时可为空，调查期间逐份登记。
        evidences = self.validate_evidences(p.get("evidences", []), minimum=0)
        p["evidences"] = self.stamp_evidences(evidences, EVIDENCE_SOURCE_BY_STATE[INITIAL_STATE])
        count, total_pages = self.evidence_totals(p["evidences"])
        p["evidence_count"] = count
        p["total_evidence_pages"] = total_pages
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
        if action == "register_evidence":
            if record["state"] not in EVIDENCE_REGISTER_ROLES:
                raise Conflict("当前状态不允许登记证据")
            return record["state"]
        allowed = TRANSITIONS.get(action, {}).get(record["state"])
        if allowed is None:
            raise Conflict("当前状态不允许执行%s" % action)
        return allowed

    def apply_action(self, record: Dict[str, Any], action: str, data: Dict[str, Any]) -> Tuple[str, Dict[str, Any], str]:
        new_state = self.require_transition(record, action)
        data = dict(data or {})
        p = dict(record["payload"])
        changes: Dict[str, Any] = {}
        summary = ""
        if action == "register_evidence":
            evidences = self.validate_evidences(data.get("evidences"), minimum=1)
            existing = list(p.get("evidences", []))
            source = EVIDENCE_SOURCE_BY_STATE[record["state"]]
            stamped = self.stamp_evidences(evidences, source, start_seq=len(existing) + 1)
            merged = existing + stamped
            changes["evidences"] = merged
            count, total_pages = self.evidence_totals(merged)
            changes["evidence_count"] = count
            changes["total_evidence_pages"] = total_pages
            summary = "%s证据%s份（%s页）" % (source, len(stamped), sum(int(item["pages"]) for item in stamped))
        elif action == "investigate":
            changes["investigation_plan"] = text(data, "plan")
            summary = "进入稽查调查"
        elif action == "propose":
            # 提出处理建议前，证据清单必须登记完整：至少一份合规证据。
            evidences = p.get("evidences", [])
            if not isinstance(evidences, list) or not evidences:
                raise ValidationError("证据尚未登记完整，不能提出处理建议")
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
            changes["locked"] = True
            summary = "案件已结案，证据和金额已锁定"
        p.update(changes)
        return new_state, p, summary or ("已执行%s" % action)
