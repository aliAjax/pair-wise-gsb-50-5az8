# 税务稽查案件与复议流程

纯Python标准库实现的税务稽查案件与复议流程原型，使用SQLite持久化，HTTP接口由`http.server`提供。

## 模块结构

- `app.py`：命令行参数、依赖组装和服务启动。
- `src/domain.py`：领域数据类型、错误和基础校验。
- `src/rules.py`：状态转换、补税、滞纳金、处罚和证据完整性和冲突检查。
- `src/repository.py`：SQLite建表、事务和查询。
- `src/service.py`：用例编排、权限检查、乐观并发和审计。
- `src/http_api.py`：HTTP路由与统一错误响应。
- `src/audit.py`：事件时间线。
- `static/index.html`：最小演示页面。
- `tests/`：完整流程、规则计算和失败场景测试。

## 启动

```bash
python3 app.py --db ./data.db --port 8326
```

默认端口为`8326`，默认数据库位于项目目录。服务启动时自动建表。

## 主要接口

- `GET /health`：健康检查。
- `GET /`：演示页面。
- `GET /api/records`：记录列表，可带`state`和`limit`参数。
- `GET /api/records/{id}`：记录详情。
- `GET /api/records/{id}/audit`：审计时间线。
- `GET /api/stats`：状态统计。
- `GET /api/evidence-types`：证据类型目录（规定允许的8类证据代码与名称）。
- `POST /api/records`：创建记录，请求体为`{"reference":"...","data":{...}}`。
- `POST /api/records/{id}/actions/{action}`：执行业务动作，请求体为`{"expected_version":1,"data":{...}}`。

### 证据登记

每份证据登记为清单中的一条，含`type`（类型）和`pages`（页数，正整数），可选`title`（名称）。类型只能取`GET /api/evidence-types`返回的8种：书证`documentary`、物证`physical`、视听资料`audio_visual`、电子数据`electronic_data`、证人证言`witness_testimony`、当事人陈述`party_statement`、鉴定意见`expert_opinion`、勘验笔录/现场笔录`inquest_record`。

- `add_evidence`：登记/补录证据，`data`为`{"evidences":[{"type":"documentary","title":"销售合同","pages":12}]}`。调查阶段（`investigating`）仅稽查人员可登记，复议阶段（`appealed`）仅纳税人代表可补录，自动标记证据序号与来源阶段。
- `propose`（提出处理建议）前必须把证据登记完整：证据份数须等于创建时申报的`evidence_count`，否则422拒绝。
- 案件一旦`close`结案，状态、证据清单和全部金额立即锁定，任何补录或改动返回409。

除`/health`和`/`外，请求需提供`X-User-Id`、`X-Role`，可选`X-Org`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整流程、规则计算、重复引用、权限拒绝和版本冲突。
