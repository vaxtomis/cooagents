# Hermes Agent 集成细节

本文档只作为 `cooagents-setup` 失败诊断时的背景材料。正常安装和集成都应优先调用统一部署入口：

```bash
python scripts/deploy.py integrate-runtime --runtime hermes
python scripts/deploy.py sync-skills
```

## 当前部署行为

`integrate-runtime --runtime hermes` 会在本机完成这些动作：

- 检查 `hermes --version`
- 将 `HERMES_WEBHOOK_SECRET` 和服务访问 token 写入 `$(hermes config env-path)`
- 在 Hermes `config.yaml` 中启用 `platforms.webhook`
- 创建 `routes.cooagents`，默认只接收事件并写日志
- 将 cooagents 的 `config/settings.yaml` 更新为 `hermes.enabled=true`
- 将 Hermes 回调地址写为 `http://127.0.0.1:8644/webhook/cooagents`

Hermes route 的默认片段应保持为通知入口，而不是绑定已经删除的旧工作流 skill：

```yaml
platforms:
  webhook:
    enabled: true
    extra:
      host: 127.0.0.1
      port: 8644
      routes:
        cooagents:
          events: ["*"]
          secret: "${HERMES_WEBHOOK_SECRET}"
          skills: []
          prompt: |
            cooagents push event: {event_type}
            run_id: {run_id}
            ticket: {ticket}

            payload: {payload}
          deliver: "log"
```

`sync-skills` 仍会把当前仓库的 `skills/` 同步到 `~/.hermes/skills`。这些 skill 是用户可调用的安装/升级入口，不再承担事件工作流决策。

## 故障排查速查

| 症状 | 排查方向 |
|------|----------|
| Hermes gateway 无法收到事件 | 检查 `hermes gateway status`，确认 8644 端口可用 |
| Hermes 日志出现 HMAC mismatch | 对比 cooagents `.env` 与 Hermes env 中的 `HERMES_WEBHOOK_SECRET` |
| cooagents 内置订阅未创建 | 确认 `config/settings.yaml` 中 `hermes.webhook.enabled=true` 后重启 cooagents |
| skill 未同步到 Hermes | 执行 `python scripts/deploy.py sync-skills`，确认 `~/.hermes/skills/<skill>/SKILL.md` 存在 |
| 回调路径 404 | 使用 `/webhook/cooagents`，不要写成 `/webhooks/cooagents` |

## OpenClaw 与 Hermes 并存

当 `--runtime both` 时：

- OpenClaw 使用 `http://127.0.0.1:<gateway_port>/hooks/agent`
- Hermes 使用 `http://127.0.0.1:8644/webhook/cooagents`
- `sync-skills` 会把同一份 `skills/` 同步到 OpenClaw 和 Hermes 的技能目录
