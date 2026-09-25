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
- `POST /api/records`：创建记录，请求体为`{"reference":"...","data":{...}}`。
- `POST /api/records/{id}/actions/{action}`：执行业务动作，请求体为`{"expected_version":1,"data":{...}}`。

## 证据清单

每份证据逐份登记，存放在记录的`payload.evidences`中，每项包含：

- `type`：证据类型，只能取以下法定种类之一：`书证`、`物证`、`视听资料`、`电子数据`、`证人证言`、`当事人陈述`、`鉴定意见`、`勘验现场笔录`。
- `pages`：页数，正整数。
- `title`：可选标题。
- 系统自动补充`seq`（顺序号）和`source`（`稽查登记` / `纳税人补录`）。

同时维护派生字段`evidence_count`（证据份数）与`total_evidence_pages`（总页数）。

证据登记动作`register_evidence`不改变案件状态，请求体：

```json
{"expected_version": 2, "data": {"evidences": [{"type": "书证", "pages": 12}]}}
```

登记规则：

- 稽查人员（`inspector`）可在`opened`、`investigating`阶段登记；提出处理建议（`propose`）前必须至少登记一份证据。
- 复议期间（`appealed`）由纳税人代理（`taxpayer_rep`）补录新证据，稽查人员不能再登记。
- 案件结案（`closed`）后证据清单与金额全部锁定，任何人不能再补录或改动，接口返回`conflict`。

除`/health`和`/`外，请求需提供`X-User-Id`、`X-Role`，可选`X-Org`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整流程、规则计算、重复引用、权限拒绝和版本冲突。
