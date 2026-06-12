# Hermes Agent Deployment Runbook

This VPS currently uses a direct install at `/home/lighthouse/.hermes/hermes-agent`.
Keep secrets in `/home/lighthouse/.hermes/.env`; do not paste secret values into logs, tickets, or README files.

## Current Audit Snapshot

- OS: Ubuntu 24.04.4 LTS, kernel `6.8.0-117-generic`.
- CPU/RAM: 2 vCPU, 3.6 GiB RAM, 1.9 GiB swap.
- Disk: root filesystem 59 GiB, 68% used at audit time.
- Listening ports: SSH on `0.0.0.0:22` and `[::]:22`; no Hermes public TCP listener found.
- Install path: `/home/lighthouse/.hermes/hermes-agent`.
- Hermes package version: `0.8.0` from `pyproject.toml`.
- Git state: `main`, remote `origin`, current commit `45034b74`; `package-lock.json` has local modifications.
- Runtime: one interactive CLI process is attached to an SSH TTY; one gateway process is running as root under `user@0.service`.
- Logs: `/home/lighthouse/.hermes/logs/agent.log` and `/home/lighthouse/.hermes/logs/errors.log`.
- Docker/Compose/pm2/tmux/screen: not active or not installed during audit.
- Python/Node: system Python 3.12.3, app venv Python 3.11.15, Node 22.22.2, npm 10.9.7, uv 0.11.6.
- `.env`: present at `/home/lighthouse/.hermes/.env`; only variable names were audited.

## Risks Found

1. High: gateway is running as root from `/root/.hermes`, while the main install and secrets are owned by `lighthouse`.
2. High: the interactive Hermes process depends on an SSH session (`pts/2` with `SSH_CONNECTION`/`SSH_TTY`).
3. High: many Hermes project and data directories are world-writable (`777`), including code, venv, skills, hooks, and caches.
4. Medium: no system-wide Hermes service was found; only a root user service exists for gateway.
5. Medium: no Hermes-specific logrotate config was found.
6. Medium: no healthcheck was found.
7. Medium: backup automation was not found; root crontab/firewall details could not be fully inspected without root.
8. Medium: update path is partially uncontrolled; venv Python has no `pip`, and the git worktree has local changes.

## Deployment Options

### A. Minimal Change

- Leave the current install in place.
- Stop relying on SSH for long-running gateway work.
- Add healthcheck, backup script, update script, and logrotate.
- Tighten permissions after taking a backup:

```bash
chmod -R go-w /home/lighthouse/.hermes/hermes-agent /home/lighthouse/.hermes/skills /home/lighthouse/.hermes/hooks
chmod 700 /home/lighthouse/.hermes /home/lighthouse/.hermes/logs /home/lighthouse/.hermes/memories /home/lighthouse/.hermes/sessions
chmod 600 /home/lighthouse/.hermes/.env /home/lighthouse/.hermes/config.yaml /home/lighthouse/.hermes/auth.json
```

### B. Recommended

Run the gateway as the `lighthouse` user with systemd user services:

```bash
mkdir -p ~/.config/systemd/user
cp /home/lighthouse/.hermes/hermes-agent/deploy/systemd/hermes-gateway.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hermes-gateway.service
loginctl enable-linger lighthouse
```

Before enabling this service, stop the root gateway to avoid duplicate gateway workers:

```bash
systemctl --user -M root@ stop hermes-gateway.service
```

If the root user-manager command is unavailable, inspect with:

```bash
systemctl status user@0.service
```

### C. Hardened

- Keep Hermes gateway private unless a documented integration requires inbound webhooks.
- Put any webhook endpoint behind Nginx/Caddy with TLS and request size limits.
- Install the logrotate config.
- Schedule backups with a user timer or cron.
- Run `deploy/healthcheck.sh` from a timer and alert on non-zero exit.
- Pin updates to git commits or reviewed release tags.
- Keep provider/model choices in existing config; do not change LLM provider during deployment hardening.

## Commands

### Health

```bash
/home/lighthouse/.hermes/hermes-agent/deploy/healthcheck.sh
```

### Backup

```bash
/home/lighthouse/.hermes/hermes-agent/deploy/backup.sh
```

Manual one-line backup:

```bash
tar --exclude='/home/lighthouse/.hermes/hermes-agent/node_modules' --exclude='/home/lighthouse/.hermes/hermes-agent/venv' -czf /home/lighthouse/hermes-backup-$(date +%F-%H%M%S).tar.gz -C /home/lighthouse .hermes
```

### Start, Stop, Restart

For the recommended user service:

```bash
systemctl --user start hermes-gateway.service
systemctl --user stop hermes-gateway.service
systemctl --user status hermes-gateway.service
```

For maintenance restarts that should *announce the reason before stopping* and *report back after recovery*, use:

```bash
python /home/lighthouse/.hermes/scripts/restart_gateway_with_notice.py \
  --reason "<human readable reason>"
```

### Logs

```bash
journalctl --user -u hermes-gateway.service -n 100 --no-pager
journalctl --user -u hermes-gateway.service -f
/home/lighthouse/.local/bin/hermes logs
/home/lighthouse/.local/bin/hermes logs errors
```

### Install Log Rotation

```bash
sudo cp /home/lighthouse/.hermes/hermes-agent/deploy/logrotate/hermes-agent /etc/logrotate.d/hermes-agent
sudo logrotate -d /etc/logrotate.d/hermes-agent
```

### Update

```bash
cd /home/lighthouse/.hermes/hermes-agent
HERMES_UPDATE_BRANCH=main ./deploy/update.sh
systemctl --user restart hermes-gateway.service
```

### Rollback

Use the backup made before the change. Stop the service first:

```bash
systemctl --user stop hermes-gateway.service
mkdir -p /home/lighthouse/rollback-hermes
tar -xzf /home/lighthouse/hermes-backups/hermes-backup-YYYY-MM-DD-HHMMSS.tar.gz -C /home/lighthouse/rollback-hermes
```

Then restore only the files you intend to roll back. Do not overwrite `sessions`, `memories`, or `state.db` unless that is the explicit rollback target.

For code-only rollback:

```bash
cd /home/lighthouse/.hermes/hermes-agent
git checkout <previous-commit>
systemctl --user restart hermes-gateway.service
```

## Files Added By This Runbook

- `deploy/systemd/hermes-gateway.service`
- `deploy/healthcheck.sh`
- `deploy/backup.sh`
- `deploy/update.sh`
- `deploy/logrotate/hermes-agent`
- `README-DEPLOY.md`

## Troubleshooting: AGY 429 Quota/Rate Limit Handling Fix (2026-05-30)

### 现象 (Incident / Symptoms)
* 在 Telegram 中向 Hermes bot 发送 `/new` 及请求时，如果底层 Antigravity / Gemini API 触发 HTTP 429 配额限制，用户界面会陷入连续返回 `Waiting for the background task to complete` 提示。
* 客户端未触发 Cooldown，而是在不断重试后最终以 `timed out waiting for response`（超时挂起）报错结束，导致极其糟糕的用户体验。

### 根因 (Root Causes)
1. **Bridge 状态行污染**: `/home/lighthouse/.hermes/bin/agy_acp_bridge.py` 实时读取 `agy` 进程的 stdout 时，没有过滤 `Waiting for the background task to complete` / `Last progress:` / `timed out waiting for response` 等状态行，导致这些信息作为正常的 `agent_message_chunk` 被推送并污染了输出流。
2. **错误未结构化分类**: 当 `agy` 退出且 stdout/stderr 出现 429/配额限制时，Bridge 仅仅返回了普通的 500 error，其 `error.data` 未能结构化识别（如 HTTP Status、Model、Provider、Error Type），使得上层无法准确断定这是 `quota_exceeded`。
3. **退避重试过多**: `run_agent.py` 对普通的非 401/403 错误（如未被识别为 quota 的 429）会默认重试 3 次，导致无法直接阻断并快速返回，一直等待到全局超时。
4. **Cron Fallback 未透传**: 在 `cron/scheduler.py` 中，初始化 `AIAgent` 没能完整读取并透传 `fallback_model` 链，使得复杂 Cron 任务失败后没有回退路径。

### 修复内容 (Fixes Implemented)
1. **Bridge 改动 (`agy_acp_bridge.py`)**:
   * 将 `stderr` 改为 `subprocess.STDOUT` 合并输出，防止高频输出时子进程卡死。
   * 新增 `get_safe_message()` 机制拦截并屏蔽敏感 Key/Token，保证诊断信息安全。
   * 实时过滤状态行，非状态内容才通过 `agent_message_chunk` 推送。
   * 识别 429、Timeout 关键字并发送结构化 `ProviderError`（带 http_status 429 / error_type `quota_exceeded`）。
2. **客户端与分类改动 (`copilot_acp_client.py` & `error_classifier.py`)**:
   * 解析 JSON-RPC `error.data` 中的 http_status 与 error_type。
   * 明确指定 429 `quota_exceeded` 行为：设定 `should_rotate_credential = False`（不执行密钥轮转），但由于其代表配额受限，由 AIAgent 捕获。
3. **决策层改动 (`run_agent.py`)**:
   * 当捕获到 `quota_exceeded` / `rate_limited` 且无 Fallback 时，**立即退出迭代**，不进行二次重试，直接输出翻译好的中文友好原因：
     > 模型调用失败：HTTP 429。原因：当前 Antigravity/Gemini 模型额度或速率限制已触发。本次请求已停止重试；如配置了 fallback，可切换备用模型，否则请等待额度恢复。
4. **计划任务改动 (`cron/scheduler.py`)**:
   * 在 AIAgent 启动时正确加载并透传 `fallback_model` 链。

### 验证命令
#### 1. 模拟 429 环境验证
将 `.env` 配置文件中的 `HERMES_ANTIGRAVITY_CLI` 改为 mock 429 报错脚本（只输出 `HTTP 429: The usage limit has been reached` 并退出码为 1），随后重启网关或直接在 CLI 执行：
```bash
HERMES_COPILOT_ACP_COMMAND=/home/lighthouse/.hermes/bin/agy_acp_bridge.py \
venv/bin/python run_agent.py \
--base_url="acp://antigravity" \
--model="models/gemini-flash-latest" \
--query="hello"
```
* **预期行为**: CLI 立即在 1 次迭代内退出，不发起 3 次重试，直接输出中文 quota 说明且不产生状态行挂起。
* **单元测试命令**:
  ```bash
  venv/bin/python /home/lighthouse/.gemini/antigravity-cli/brain/7f168220-ce36-4f96-b4dc-413b31444fb2/scratch/test_quota_error_handling.py
  ```

#### 2. 真实回归命令
将 `HERMES_ANTIGRAVITY_CLI` 指回真实 agy（`/home/lighthouse/.local/bin/agy`）：
```bash
HERMES_COPILOT_ACP_COMMAND=/home/lighthouse/.hermes/bin/agy_acp_bridge.py \
venv/bin/python run_agent.py \
--base_url="acp://antigravity" \
--model="models/gemini-flash-latest" \
--query="hello, respond with only 'OK'"
```
* **预期行为**: 耗时 4-5 秒，直接干净输出 `OK`，表示回归测试正常。

### 回滚命令
如需回退本次全部修改，请执行：
```bash
# 1. 停止网关
sudo XDG_RUNTIME_DIR=/run/user/0 systemctl --user stop hermes-gateway.service

# 2. 恢复 Bridge 脚本备份
cp /home/lighthouse/.hermes/bin/agy_acp_bridge.py.bak /home/lighthouse/.hermes/bin/agy_acp_bridge.py

# 3. Git 放弃 hermes-agent 下的更改
cd /home/lighthouse/.hermes/hermes-agent
git checkout -- agent/copilot_acp_client.py agent/error_classifier.py cron/scheduler.py run_agent.py

# 4. 重启网关
sudo XDG_RUNTIME_DIR=/run/user/0 systemctl --user restart hermes-gateway.service
```

## 第二阶段修复记录（2026-05-30）

### 现象
1. `antigravity-acp` 无响应时等待 3 × 180s（总计 9 分钟）才失败。
2. Telegram 持续推送 `Still working...` 和 `No response from provider for 180s` 刷屏。
3. cronjob 将 "I have launched..." 等 pending 过渡文本作为最终任务结果推送到 Telegram。
4. fallback 配置含无效模型（`gpt-5.4-mini`、`models/gemini-flash-lite-latest` via direct gemini），导致 fallback 链实际无效。
5. cron 任务未透传 `fallback_model` 给 `resolve_turn_route`。

### 根因
- `run_agent.py` 的 stale stream 超时默认 180s，antigravity-acp 无法短路。
- 无法区分 antigravity-acp 是否已经超时过一次，导致 gateway 持续刷"Still working"。
- `cron/scheduler.py` 未从 `_cfg` 读取全局 `fallback_providers`，亦未注入 primary dict 中。
- `config.yaml` 的 `fallback_providers` 包含未验证的模型名。

### 修复内容

#### 1. `config.yaml`（`/home/lighthouse/.hermes/shared/config.yaml`）
- `fallback_providers`：删除 `gpt-5.5 (openai-codex)` 和 `models/gemini-flash-lite-latest (gemini)`，改为唯一有效的 `gpt-5.3-codex (openai-codex)`。
- `smart_model_routing.cheap_model.fallback_providers`：同步清理无效条目。

#### 2. `run_agent.py`
- `antigravity-acp` 的 stale stream 超时从 180s 降至 90s（可通过 `HERMES_ANTIGRAVITY_STALE_TIMEOUT` 覆盖）。
- 大 context 扩展超时仅对非 antigravity-acp 生效。
- 首次"No response"警告后设置 `self._has_emitted_no_response_warning = True`。
- `antigravity-acp` + timeout 类错误在 1 次重试后立即尝试 fallback，避免 3×90s 等待。
- 新增 `_activated_fallbacks_count` / `_has_emitted_no_response_warning` 初始化（`__init__` 和 `switch_model` 中重置）。

#### 3. `gateway/run.py`
- `_notify_long_running()` 中：若当前 provider 为 `antigravity-acp` 且 `_has_emitted_no_response_warning` 为 True，跳过该轮"Still working"推送。

#### 4. `cron/scheduler.py`
- 从 `_cfg` 读取 `fallback_providers` / `fallback_model` 并注入 primary route dict，再用 `turn_route.get("fallback_model")` 为 AIAgent 提供 fallback 链。
- 新增 `_PENDING_PATTERNS` 列表（含"I have launched…""background task…""已启动"等）。
- 在 deliver_content 过滤阶段：若成功且 response 内包含 pending 短语则跳过推送，避免将过渡消息作为最终 cron 结果推送到 Telegram。

### 验证命令
```bash
# 语法检查
cd /home/lighthouse/.hermes/hermes-agent
venv/bin/python -m py_compile cron/scheduler.py run_agent.py gateway/run.py

# 重启服务
sudo XDG_RUNTIME_DIR=/run/user/0 systemctl --user restart hermes-gateway.service

# 验证状态
sudo XDG_RUNTIME_DIR=/run/user/0 systemctl --user status hermes-gateway.service --no-pager
```

### 第二阶段回滚命令
```bash
cd /home/lighthouse/.hermes/hermes-agent
git checkout -- run_agent.py gateway/run.py cron/scheduler.py

# 恢复 config.yaml 的 fallback 配置（手动或从备份）
# 备份：config.yaml.bak-20260528-161401
sudo XDG_RUNTIME_DIR=/run/user/0 systemctl --user restart hermes-gateway.service
```
