# Hermes Model Routing Strategy

> 本文档是所有模型/Provider/路由/配额修改工作的**唯一权威规范**。
> 修改任何 model switching、provider registry、fallback、quota/cooldown 逻辑前，必须先读本文档。
> 修订记录见文末 `## Changelog`。若代码行为与本文档冲突，以本文档为准并同步修正代码或文档。

---

## 0. 目标

模型调度的目标不是「更多选项」或「更长的 fallback 链」，而是：

1. **简单**：高频选择只露常用项；诊断放到显式命令后面。
2. **清晰**：把 `alias`、`display label`、`provider`、`raw model id`、`quota family` 五者分开，任何表面不自行发明命名。
3. **高可用**：短 fallback 链、快速健康过滤、不打已知坏路径。
4. **配额高效**：把配额当作**调度信号**（执行前决策），而非**错误信号**（429 之后才反应）。

---

## 1. 五层模型

路由共 5 层，排查/重构时必须分清，禁止跨层混淆。

| 层 | 职责 | 关键文件/模块（当前 release） |
|----|------|------------------------------|
| **1 Registry** | 定义可执行模型条目：唯一 alias、family、provider、raw model id、quota_family、task tags、priority、context、supports_execute | `config.yaml.providers.*`、`hermes_cli/models.py`、`plugins/model-providers/*` |
| **2 Quota / Health** | 判定一个 slot 当前是否可用；规范化 quota/auth/近 429/重置/冷却 | `agent/route_health.py`、`hourly_quota_status_pretty.py`（写健康态） |
| **3 Selector** | 为某 task profile 选 alias，输出选中+fallback 链+原因；不构建 UI 文案 | `agent/agent_runtime_helpers.py`、`agent/agent_init.py` |
| **4 Execution** | 精确执行选中 alias；runtime bundle 随行；失败回写 health/cooldown | `agent/chat_completion_helpers.py`（`try_activate_fallback`）、`agent/auxiliary_client.py` |
| **5 UI / Command** | 快速查看与切换；`/model` 默认是快捷面板不是 registry 转储 | `hermes_cli/models.py`、`gateway/slash_commands.py`、`plugins/platforms/telegram/adapter.py` |

### 1.1 三层模型身份（关键）

任何一次调用都有**三个不同**的身份，排查「为什么 footer 显示 X」时必须区分：

- **requested**：用户/配置请求的 alias（如 `gemini-3.6-flash-high`）。
- **selected**：selector 决策后选中的 alias（可能因配额/健康被改写）。
- **executed**：实际执行的 `(provider, raw_model_id, base_url)` 三元组。

`hermes status` / `config.yaml` 报告的是**持久化默认**（requested/selected 层）；
Telegram footer 报告的是**该次回复实际执行**（executed 层）。
两者可以不同（如 session override、fallback 激活），不是 bug。

---

## 2. 命名规则

五种名称必须分开，禁止混用：

| 名称 | 含义 | 示例 | 存放处 |
|------|------|------|--------|
| **alias** | 唯一稳定存储值 | `volcengine-coding-ark-code-latest` | config、callback_data、session override、cron pin |
| **display label** | 短人类标签 | `Volc Coding` | Telegram/Discord/CLI 按钮 |
| **provider** | 路由/传输方 | `volcengine-coding-plan` | config、footer |
| **raw model id** | 上游 API/bridge 模型串 | `ark-code-latest` | 调用执行 |
| **quota family** | 配额分组键 | `volcengine_coding_plan` | 健康/展示 |

规则：
- config、callback_data、session override、cron pin 存 **alias 或显式 provider/model**，不存 display label。
- 同一 raw model id 出现在多 provider/plan 下 → `resolve_model_input("ark-code-latest")` 应报**歧义**，不猜。
- `antigravity-acp` 是 **label 翻译 bridge**：Hermes raw id（`gemini-3.6-flash-high`）与 `antigravity-usage` 显示标签（`Gemini 3.6 Flash (High)`）是两个命名空间。

---

## 3. Provider 契约（当前有效路由池）

### 3.1 执行 provider（可跑 agent turn）

| Provider | base_url | 模型别名 | context | 说明 |
|----------|----------|---------|---------|------|
| `antigravity-acp` | `acp://antigravity` | `gemini-3.6-flash-high/medium/low`, `gemini-3.1-pro-*`, `claude-sonnet-4-6` | 1M（AGY） | label 翻译 ACP bridge；`discover_models: false` |
| `volcengine-coding-plan` | `.../api/coding/v3` | `ark-code-latest`, `deepseek-v4-pro`, `deepseek-v4-flash`, `glm-5.2`, `kimi-k2.7-code`, `doubao-seed-2.1-turbo` | 1M（多数）/256K（kimi、doubao-turbo） | Coding Plan，API Key 专用 |
| `volcengine-agent-plan` | `.../api/plan/v3` | `ark-code-latest`, `glm-5.2`, `deepseek-v4-pro`, `deepseek-v4-flash`, `doubao-seed-evolving`, `kimi-k2.7-code` | 同上 | Agent Plan，API Key 专用 |

**关键**：Coding Plan 与 Agent Plan 的 API Key **互不通用**，base_url 路径不同（`/coding/v3` vs `/plan/v3`）。二者是**独立配额族**，`volcengine_coding_plan` 的 429/耗尽**不得** gate `volcengine_agent_plan`。

### 3.2 fallback_providers（当前顺序，已按 provider 分组）

```
volcengine-agent-plan/glm-5.2
volcengine-agent-plan/ark-code-latest
volcengine-agent-plan/kimi-k2.7-code
volcengine-coding-plan/ark-code-latest
volcengine-coding-plan/kimi-k2.7-code
volcengine-coding-plan/doubao-seed-2-1-pro-260628
volcengine-coding-plan/glm-5.2
antigravity-acp/gemini-3.6-flash-high
antigravity-acp/gemini-3.6-flash-medium
```

- 主链 `model.default = gemini-3.6-flash-high` @ `antigravity-acp`。
- **分组规则**：同 provider 的 fallback 连续排列，避免跨 provider 触发 ACP 子进程重置（每次 15-30s 开销）。

### 3.3 辅助任务（auxiliary）契约

| 任务 | provider | model |
|------|----------|-------|
| vision | `antigravity-acp` | `models/gemini-flash-latest` |
| web_extract / compression / mcp / title_generation | `volcengine-coding-plan` | `glm-5.2` |
| skills_hub / approval / tts_audio_tags / triage_specifier | `auto` | — |

注意：auxiliary `auto` 会走默认 auto 链，可能选中意外 provider；若需确定性路由应改为显式 provider。

---

## 4. 配额 / 健康层

### 4.1 状态语义（`agent/route_health.py`）

| 状态 | 含义 | 路由行为 |
|------|------|---------|
| `healthy` | 正常可用 | 正常 |
| `degraded` | 可留但降优先级 | 保留，降权 |
| `cooldown` | 冷却中，`now < cooldown_until` | **跳过** |
| `auth_failed` | 凭证失效 | **跳过**直至修复 |
| `unknown` | 不确定 | 不显示为 100%，按低置信可用 |

**核心原则**：`config.yaml` 描述**候选池**；`route_health` 决定候选**此刻**是否可用。临时耗尽的 provider **不要从 fallback_providers 删除**，否则无法自动恢复。

### 4.2 写入方

`hourly_quota_status_pretty.py` 应把判定写进 `~/.hermes/cron/state/model_route_health.json`，而不只是发 Telegram 提醒。

### 4.3 读取方（必须统一用同一 filter）

1. 运行时 fallback（`try_activate_fallback`）
2. `cron_model_routing.routes`
3. Telegram/CLI `/model` 配额徽章
4. auxiliary/task fallback
5. cron footer（计划 vs 实际）

---

## 5. transient 429 vs 配额耗尽（2026-08-10 修正）

**这是本次梳理最重要的结论。** 此前把两类错误混为一谈导致反复误报：

| 类型 | 含义 | 恢复 | 处理 |
|------|------|------|------|
| **月度/周度/5h 额度耗尽** | 硬性额度上限 | 等到重置时间 | `cooldown` 到 reset；渲染 `🔴 0%（...额度耗尽）` |
| **transient 429（server overload / too frequent）** | 临时抖动，非额度用尽 | 秒/分钟级自动恢复 | **不**设 is_critical、**不**渲染 0%；渲染 `🟡 实时限流`；仅 15 分钟窗口内有效 |

### 5.1 判定规则（`recent_volcengine_events`）

- quota 耗尽（monthly/weekly/5h）→ 宽回溯窗口（180min）+ 带 reset 时间 + 硬 `🔴 0%`。
- transient 429 → **15 分钟**有效窗口 + `🟡 实时限流` + **不**触发 `is_critical`。
- 429 之后若有成功请求（恢复信号）→ 丢弃该 429 信号。
- 两者通过事件类型严格区分，绝不在同一 traceback 块混判。

### 5.2 缓存兜底（Codex 等 API 额度查询）

`get_recent_codex_snapshot_from_db` 缓存兜底**必须覆盖所有异常状态**（`error`、`missing`、`expired`），不只 `error`。偶发读取失败但有近期（≤4h）成功采样时，用缓存而非直接报 0%。

---

## 6. 选择器 / fallback 执行规则

1. **先硬过滤**：未认证、quota 0/N/A、冷却中、`supports_execute=false` 跳过。
2. **同 route group 优先**，再跨 group。
3. **避免 fallback 链交错 ACP bridge**：同 provider 连续（见 §3.2）。
4. **backend identity 去重**：`agent.backend_identity.should_skip_candidate` 防止 fallback 到同一后端造成环路。
5. **route_health 过滤**：`try_activate_fallback` 在构建 client 前调用 `is_route_blocked` 跳过冷却中条目。
6. **failover 冷却**：离开主 provider 遇 429/billing 时 `_rate_limited_until = now + 60s`。
7. **链耗尽防风暴**：非 429/billing 的链耗尽触发 `_FALLBACK_EXHAUSTED_COOLDOWN_S` 冷却，防 #24996 跨 turn 重放风暴。

---

## 7. /model 菜单契约

- 五组收敛（用户偏好）：`codex`、`agy-gemini/paid`、`agent-plan`、`code-plan`、`agy-oss/claude`。AI Studio（免费 gemini）**已移除**。
- `PROVIDER_GROUPS` vs `TRIMMED_MODEL_PICKER_GROUPS` 必须一致；callback handler 用后者查组。
- Telegram footer 由 `agent_result.get("model")` 渲染 → 报告该次回复实际执行模型。
- `provider="custom"`（自定义 base_url）时，footer 需按 `base_url` 路径（`/api/plan/` vs `/api/coding/`）推断 plan tag，不能只看 provider 名。

---

## 8. 已知坑位（必读）

- **session override 持久化**：当前 gateway 的权威路由存储是 `~/.hermes/state.db` 的 `gateway_routing` 表（按 `scope` 区分，生产 scope 通常为 `~/.hermes/sessions`），并保留 `~/.hermes/sessions/sessions.json` 作为 legacy mirror；`sessions.model` / `session_model_usage` 不是当前路由 override 的唯一来源。清理 stale override 必须同时核对并更新/删除 `gateway_routing` 与 `sessions.json`，操作前先备份 DB。
- **autostash 吞 provider 代码**：`hermes update`/`git pull` 可能把本地 provider 定制 stash 掉；先 `git stash list` 再诊断 provider 消失，勿归咎为「用户删除」。
- **config models 格式**：`providers.<id>.models` 必须是 `{model_id: {context_length: N}}` 的 dict，不能是 list-of-dict 或 JSON 字符串，否则 picker 「Model not found」。
- **dotted model id 写 config**：`gemini-3.6-flash-high` 里的点会被当 YAML 嵌套；用 `hermes config set` 写标量或直接编辑 yaml。
- **AGY argv 长度**：`HERMES_ANTIGRAVITY_MAX_PROMPT_CHARS` 超 30K 会导致 `[Errno 7] Argument list too long`；保持 ~24-30K。
- **AGY empty output 视为失败**：subprocess exit 0 但 assistant 空 = 无效响应，触发 fallback，不得归一化为空 assistant 消息。
- **MOA 是虚拟 provider**：`moa` 多模型 fan-out，`model.default: default` 是 preset 名不是模型 ID；1M context 是虚拟窗口。

---

## 9. Changelog

| 日期 | 变更 |
|------|------|
| 2026-08-10 | 重建本文档（原文件丢失）。收录五层模型、三层身份、transient 429 vs 配额耗尽分离、缓存兜底全状态覆盖、fallback 分组/去重/冷却规则。 |
| 2026-08-10 | 按当前 release 校正 session override 持久化说明：权威来源为 `gateway_routing` + `sessions.json` mirror；`sessions.model` 与 `session_model_usage` 不作为唯一 live route 来源。 |
