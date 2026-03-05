# cooagents

OpenClaw / Claude / Codex 协作流程模板仓库。

## 角色分工
- OpenClaw：需求沟通确认、任务分配、流程 gate
- Claude：需求理解、功能设计
- Codex：编码实现、测试与提交

## 流程概览
1. 需求确认（OpenClaw）→ 输出 `docs/req/REQ-<ticket>.md`
2. 设计阶段（Claude）→ 输出 `docs/design/DES-<ticket>.md` + ADR
3. 开发阶段（Codex）→ 输出代码 + `docs/dev/TEST-REPORT-<ticket>.md`

详见：`docs/PROCESS.md`
