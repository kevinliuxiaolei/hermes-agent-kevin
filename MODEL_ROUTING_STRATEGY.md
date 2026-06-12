# 模型路由与选择策略 (v3.10)

> Version: 3.10.0
> 目标：用一套统一、可解释、可验证的模型路由系统，同时达成 **简洁、清晰、可用率高、quota 利用率高**。用户界面只暴露少量高频选择；底层保留完整 registry、quota、health、fallback 与审计能力。

## 2026-06-12 v3.10 认证门禁、Picker 统一与压缩可观测性

在 v3.9 的生命周期契约基础上，补齐了几个仍会影响用户感知和恢复性的细节：

1. 401 / 403 认证失败不再依赖固定冷却自动恢复，必须通过显式成功执行或重新登录清除。
2. Telegram 模型按钮与 `/model` 文本统一使用同一套 quota/health 排序，不再展示静态全量 registry。
3. `custom` 运行时身份会还原成实际的 Volcengine 计划身份，避免健康状态和路由统计失真。
4. 配置 fallback 在进入有界 RoutePlan 前会先过滤已被健康门禁封锁的路由，避免占用有限槽位。
5. 压缩/辅助任务会记录 RoutePlan 候选与跳过原因，便于判断是路由问题、额度问题还是执行问题。

## 2026-06-11 v3.5 RoutePlan 执行契约

真实 Cron 429 故障证明：高 quota 模型即使已被 Registry 和 Selector 识别，如果没有进入该入口实际传给执行层的 fallback chain，仍然无法接管任务。因此系统优化优先级调整为先统一路由计划，再优化健康门禁与打分。

所有生产入口统一通过 `agent/route_plan.py` 构造执行计划：

- `auto`：自动选择并生成有界 fallback。
- `pin_with_fallback`：显式路由固定 primary，但仍生成完整 fallback；这是显式选择的默认模式。
- `strict_pin`：只执行指定 primary，失败即停止。

RoutePlan 的强制约束：

1. 配置 fallback 是有序策略偏好，不是完整执行链；其后必须合并 Registry 健康候选。
2. 当前 primary 必须从 fallback 中移除。
3. provider/model/Codex slot 相同的路由必须去重。
4. `max_fallback_candidates` 限制合并后的完整链，而不是仅限制 Registry 候选。
5. 执行层迁移期间继续接收兼容的 legacy fallback 字典，但入口不得自行拼装 Registry fallback。

P0 已完成 Cron、Gateway、CLI、Auxiliary 接入，并修复 Gateway 完整 session override 直接返回 primary-only runtime 的路径。

## 2026-06-11 v3.6 健康、Quota 与尝试预算契约

P1 将运行时约束统一为以下规则：

1. runtime cooldown 同时在 Selector 和执行时 fallback 激活阶段作为硬门禁，避免“选择时健康、执行时已熔断”的竞态。
2. provider 的可重试失败写入共享健康状态；有效响应清除该 provider 的共享失败状态。
3. `max_fallback_candidates` 限制 fallback 总数；`max_route_attempts` 限制 primary 加 fallback 的不同路由总数。
4. `agent.api_max_retries` 继续表示单条路由内部的 API 尝试次数。
5. 旧 `max_execute_attempts` 暂不作为硬门禁。历史配置值为 2，而真实恢复案例需要跨越四条路由；直接启用会降低完成率。
6. AGY 在存在可信 per-model quota 时按具体模型门控，不能再由同 family 的健康模型掩盖已耗尽模型。
7. AGY 不是单一 quota 池，而是两个物理隔离的 `HOME` 账号槽；展示、selector 与 footer 必须区分 `primary` / `secondary` 执行面，并保留 family 级聚合视图。

## 2026-06-11 v3.7 打分、Doctor 与真实故障验收

P2 收敛了显式选择、自动打分和诊断语义：

1. 显式 alias 在 runtime 与 quota 仍可用时固定为 primary，即使 quota 较低也不被自动 Selector 替换；耗尽或熔断后进入 RoutePlan fallback。
2. 自动选择先遵守 task route group，再在组内按任务适配、quota、成本和 alias priority 选择，避免跨 family 的高 quota 破坏任务画像。
3. Hermes AGY bridge `/home/lighthouse/.hermes/bin/agy_acp_bridge.py` 被 Registry 与 runtime provider 共同识别，避免出现“AGY quota 可见但普通进程认为不可执行”。
4. Doctor 验证 quota 可用 family 是否存在可执行 route，并检查 RoutePlan 的 primary 重复、fallback 重复、字段缺失和 `max_route_attempts` 超限。
5. Doctor 输出按真实执行顺序排列的 failure simulation，不再只打印裸 Selector fallback chain。

真实验收案例：

- 任务：`stock-realtime-300693-interpret`，ID `3c81eea288d5`。
- 显式 primary `volcengine-coding-plan / ark-code-latest` 返回 429。
- 共享健康门禁跳过已知 429 的 `volcengine-agent-plan` 与 Gemini direct。
- RoutePlan 激活 `antigravity-acp / models/gemini-flash-lite-latest`。
- 任务于 `2026-06-11 15:46:06 +08:00` 成功完成。

## 背景

当前系统已经可以从 cron 任务读取到各账号的准确额度信息，因此路由策略不再需要依赖“保守降级”来避免误判。现在的关键点不是“能不能看到额度”，而是：

1. 如何在多个可用账号之间做最优选择。
2. 如何在不同模型之间切换，同时避免 cron 卡住、无响应或反复打同一条失败路径。
3. 如何在保证任务执行成功率的同时，提高高质量模型和低成本模型的整体利用率。

## 设计目标

整体目标不是继续堆更多模型，而是把系统收敛成“前台简单、后台完整、失败可恢复、额度可调度”的统一架构。

1. **简洁**
   - Telegram `/model` 默认只显示当前模型、少量推荐候选和核心额度概览。
   - 高频用户只需要知道“现在用哪个、还能切哪几个、额度是否健康”。
   - 全量 registry、诊断字段、账号路径、原始 quota 明细仅在 `/model list` / `/model inspect` 中出现。

2. **清晰**
   - 每个可执行模型必须有唯一 alias；同 raw model 跨 provider / plan 时必须显式前缀化。
   - UI display label 与底层 stored alias 分离：按钮可以短，callback / config 必须精确。
   - 路由解释只回答三件事：为什么选它、为什么跳过别人、失败后下一跳是谁。

3. **可用率高**
   - 任何自动路由都必须先过硬门槛：认证可用、quota 可用、provider 健康、支持执行。
   - 每个任务画像都有明确 fallback chain，失败后快速切换，不在同一坏路径反复重试。
   - cron 以完成率优先，避免长链路、无界重试和跨后端抖动。

4. **quota 利用率高**
   - quota 是调度信号，不只是错误兜底信号。
   - 同能力组内先做账号/slot 轮转，再跨能力组升级。
   - 低价值或高频任务优先消耗稳定低成本额度；高价值任务才使用 Codex/强推理模型。
   - 额度低、近期失败或 reset 临近的 slot 降权；额度为 0 或 N/A 的 slot 跳过。

5. **策略可 review / 可验证**
   - 所有路由规则落在 registry、selector、quota、health、UI 五层，不在 prompt 或 cron 文案里散落硬编码。
   - 每次改动必须能用单测、CLI smoke test、gateway 重启日志三类证据验证。

## 术语

- **模型 family**：逻辑产品/能力族，如 `volcengine`、`codex`、`gemini`、`claude`、`gpt`。用于 UI 聚合与 route group 归类。
- **provider**：真实执行通道，如 `volcengine-agent-plan`、`volcengine-coding-plan`、`openai-codex`、`antigravity-acp`。provider 必须能唯一定位认证方式、base_url / bridge 与运行环境。
- **raw model id**：上游 API 或 bridge 接收的模型名，如 `ark-code-latest`。raw id 不保证全局唯一，不能直接作为用户可见唯一 alias。
- **model alias**：Hermes 内部和 `/model` 使用的唯一稳定选择键，如 `volcengine-agent-ark-code-latest`。alias 必须稳定、可审计、可持久化。
> - **route group**：更高层的路由组，当前全局默认顺序为 `volcengine -> codex -> gemini -> claude -> gpt -> fallback_free`。  
>   实际路由顺序按 **任务画像** 动态调整：
>   - `chat`/`background`/`cron`/`summary`/`light`：先 `volcengine`，保护 Codex/ACP 额度。
>   - `code`/`review`：先 `codex`，优先最强编程能力。
>   - `analysis`：`codex` -> `claude` -> `volcengine`。  
>   详见 "总体优先级" 和 "任务画像路由" 章节。
- **账号 slot**：同一模型家族下的具体账号/身份，例如 `CODEX_HOME` 不同、quota 不同的 Codex 账号。
- **AGY slot**：Antigravity 的具体物理账号槽；由桥接器通过重写 `HOME` 隔离，例如 `primary=/home/lighthouse` 与 `secondary=/home/lighthouse/.hermes/second_home`。
- **preferred alias**：用户显式选中的模型别名，优先级最高。
- **fallback chain**：主模型失败后，用于接力的模型链。

## 总体优先级

### 1. 显式选择优先

如果用户或会话已经指定了 `selected_model_alias`，那么这条选择优先于所有自动路由。

适用场景：

- `/model` 手动切换
- 会话级 override
- cron 中显式配置的模型

### 2. 自动选择按 route group 排序

当没有显式选择时，自动路由按以下顺序评估：

### 全局默认顺序

1. `codex`  
2. `volcengine`  
3. `gemini`  
4. `claude`  
5. `gpt-oss`  
6. `fallback_free`

### 任务画像路由（2026-06-10 落地）

为不同任务类型定义了不同的路由优先级，避免把高价值额度浪费在轻量任务上：

| 任务类型 (`task`) | 路由优先级 | 设计理由 |
|------------------|-----------|---------|
| `chat` / `background` | **volcengine → codex → gemini → claude → gpt → fallback_free** | 日常交互优先稳定低耗的 Volcengine，保护 Codex/ACP 额度 |
| `code` / `review` | **codex → volcengine → claude → gemini → gpt → fallback_free** | 编程任务优先最强模型，Codex 能力最强 |
| `cron` / `summary` / `light` | **volcengine → codex → gemini → claude → gpt → fallback_free** | 定时任务必须便宜、可预测，不烧优质额度 |
| `analysis` | **codex → claude → volcengine → gemini → gpt → fallback_free** | 深度分析允许更高成本，优先推理能力强的模型 |

## 同模型不同账号的选择

当“同一模型能力”存在多个账号 slot 时，选择逻辑应当按下面顺序处理：

1. **账号可用性**
   - 先看该账号是否有可用额度。
   - 这里使用最新 quota 结果，而不是静态默认值。

2. **任务匹配**
   - `cron` / `summary` / `light` 优先低成本、高稳定模型。
   - `code` / `review` / `analysis` 可以接受更高成本但更强能力的模型。

3. **账号健康度**
   - 最近是否发生认证失败、429、超时、无响应。
   - 失败更少、恢复更快的账号优先。

4. **会话稳定性**
   - 同一会话优先保持当前账号，避免不必要的上下文抖动。
   - 只有当当前账号额度不足、失败或任务不匹配时才切换。

5. **额度利用率**
   - 在同一能力组内优先消耗剩余额度更健康的账号。
   - 不要让某一个账号长期成为唯一默认消耗点。

### Codex 特别规则

Codex 目前要明确区分不同 `CODEX_HOME`：

- `codex_plus` 对应 `~/.codex-plus`
- `codex_business` 对应 `~/.codex-business`

这意味着“同模型不同账号”不能只看 `provider + model`，必须把账号身份一起纳入选择键，否则会把两个真实可用的账号错误合并。

## 跨模型切换路由

### 推荐切换顺序

在没有显式固定模型的情况下，路由应按如下路径尝试：

1. 当前 preferred / session override
2. 当前 route group 内的可用账号
3. 下一 route group
4. 再下一 route group
5. 最后兜底到 `gpt-oss`

### 失败类型与处理

#### 认证失败 / 账号失效

- 401 / 403 / 明确 auth failure
- 处理：立即切换到同 route group 的其他账号，或者切到下一 route group
- 不要在原账号上无限重试

#### 额度耗尽

- quota 为 0，或者 quota 读取显示不可用
- **N/A 额度按 0% 处理**（2026-06-10 修正）：Antigravity/ACP 的 `N/A` 不再当作可用/无限，必须视为 exhausted。
- 处理：直接跳过该账号
- 如果同 route group 还有其他账号，先尝试同组其他账号

#### 速率限制 / 5xx / 超时

- 处理：有限重试
- 如果重试后仍失败，切到 fallback chain 的下一项
- cron 场景下不要保留过长链路，避免任务阻塞

#### 无响应 / 空响应

- 处理：视为执行失败，尽快切换
- 不能让 cron 任务因为“空回包”反复卡在同一模型

## cron 安全约束

cron 的原则不是“尽量聪明”，而是“必须完成”。

### 必须满足

1. **单次任务有明确上限**
   - 重试次数有限。
   - fallback 链有限。

2. **不要重复打同一路由**
   - fallback chain 里同 route group 最好只保留一个候选，防止在同一后端上反复失败。

3. **账号上下文必须隔离**
   - Codex 相关的 `CODEX_HOME` 必须跟随模型 slot。
   - 不同账号不能被 dedupe 成同一个模型。

4. **显式配置优先**
   - cron 显式指定模型时，不应被自动 selector 覆盖。

### cron 的默认行为建议

- 如果用户配置了明确的模型，就直接执行。
- 如果没有显式模型，就走 selector。
- 选择失败时，立即进入 fallback chain。
- fallback chain 优先覆盖 route group，而不是堆叠同组多个近似模型。

## 利用率优化原则

### 1. 先消耗“合适但便宜”的模型

对于 `light`、`summary`、`cron`：

- 优先低成本模型。
- 只有在额度不足或任务不匹配时才升级。

对于 `code`、`review`、`analysis`：

- 允许更强的模型优先，但仍然保留 route group 顺序。
- 先在当前组里找可用账号，再切换到下一组。

### 2. 不要把 fallback 变成“同后端重复尝试”

如果 fallback chain 的前几项都来自同一个 backend，只会增加延迟，不会增加成功率。

因此建议：

- 每个 route group 只留一个候选。
- 候选要代表“当前组最适合的账号 slot”。

### 3. 用额度来做“动态分流”

既然 quota 是准确的，就应该让 quota 参与选择，而不是只做兜底。

建议策略：

- 额度高且任务匹配的账号优先。
- 额度接近耗尽的账号降权。
- 额度为 0 的账号直接跳过。

### 4. 保持会话稳定

同一会话中，除非发生以下情况，否则不主动切换：

- 当前账号失败
- 当前账号额度不足
- 当前任务类型明显变化
- 用户显式切换模型

这样可以减少上下文抖动，也能减少“看起来路由很聪明，但实际上把会话切碎”的问题。

## 建议的选择算法

可以把选择逻辑理解成四层打分：

1. **硬门槛**
   - 是否 enabled
   - 是否 supports_execute
   - 是否 quota available（含 N/A 按 0% 处理）

2. **任务画像路由优先级**
   - 按 `task` 类型动态选择 route group 顺序（见上方"任务画像路由"）
   - 默认全局顺序：`volcengine → codex → gemini → claude → gpt → fallback_free`

3. **任务适配度**
   - `light/summary/cron` 偏低成本
   - `review/code/analysis` 偏能力

4. **账号健康与利用率**
   - 最近错误更少优先
   - 剩余额度更健康优先
   - 同一会话优先保留当前账号

## 当前已落地的代码位置

- [agent/model_registry.py](/home/lighthouse/.hermes/hermes-agent/agent/model_registry.py)
- [agent/model_selector.py](/home/lighthouse/.hermes/hermes-agent/agent/model_selector.py)
- [agent/quota_registry.py](/home/lighthouse/.hermes/hermes-agent/agent/quota_registry.py)
- [cron/scheduler.py](/home/lighthouse/.hermes/hermes-agent/cron/scheduler.py)
- [gateway/run.py](/home/lighthouse/.hermes/hermes-agent/gateway/run.py)

## 后续 review 重点

建议后续模型重点检查下面这些问题：

1. 同模型不同账号是否应该加入更细粒度的健康分。
2. route group 顺序是否需要按任务类型再分支。
3. cron 的 fallback chain 是否需要按任务类别做长度控制。
4. 是否需要把“最近成功账号”做成 session sticky，以进一步提高成功率。
5. 当 Codex / Gemini / Claude 都可用时，是否应该把低成本任务更多倾向到低成本组，减少高价值额度的浪费。

## 结论

当前最重要的不是继续扩大 fallback，而是把“准确 quota + 账号身份 + route group 顺序”统一成一套稳定的选择规则：

- 先保证任务不失败。
- 再保证切换有序。
- 最后才做成本和利用率优化。

这份策略的核心目标是：**让 cron 持续可执行，让同模型多账号真正可分流，让跨模型切换可解释、可控、可优化。**

## 2026-06-09 修复与优化闭环

在执行模型路由规则的过程中，落地了以下关键小步修复，以确保自动选择策略的稳定性：

### 1. N/A 额度处理逻辑优化（历史记录，已被 v3.1+ 覆盖）
* **当时问题**：Google Antigravity 中部分模型在无限制状态下返回 `N/A`，早期系统曾尝试把 `N/A` 解释为可用。
* **当前结论**：该策略已废弃。自 2026-06-10 起，系统统一将 `N/A` 视为不可确定/不可用，在 selector 中按 `0%` / `quota_exhausted` 处理，避免把未知额度误当作可用额度反复命中。

### 2. 诊断工具（hermes_model_doctor）排虚警
* **问题**：[hermes_model_doctor.py](file:///home/lighthouse/.hermes/hermes-agent/tools/hermes_model_doctor.py) 之前的全局配置文件残留检查会误报非模型设置（例如 `tts: model: gpt-4o-mini-tts`）里的 `gpt-4` 字符串，产生 False Positive。
* **修复**：修改为精准提取序列化 `model` 相关的子配置块（`model`、`model_aliases` 等）进行残留检查。

经过上述闭环修复，运行 `hermes_model_doctor.py` 输出的诊断状态已成功转为 **`PASS`**，且 selector 能够为不同任务分配最合理的 Gemini 物理模型。

## v2 设计与规划 (Google AI Studio & NVIDIA NIM Free 兜底)

> Target: v2.0.0
> Status: Completed

我们引入了 Google AI Studio (Gemini) 独立 API 账号池与 NVIDIA NIM 免费 API 作为系统最低优先级的兜底模型链路。为了规避免费 NVIDIA NIM 配额不稳定、高延迟或报错的问题，新增了独立的 Google AI Studio 兜底账号。

### 1. 核心模型注册 (Model Registry)
- [x] 注册 `gemini` 免费 API Provider 别名（优先级高于 NVIDIA NIM）：
  - `gemini-fallback` -> `gemini-2.0-flash` (priority 1, role="fallback_only")
  - `gemini-pro-fallback` -> `gemini-2.0-pro` (priority 2, role="fallback_only")
- [x] 注册 `nvidia_nim` 免费 Provider 别名（优先级低于 Google AI Studio）：
  - `nv-fallback` -> `deepseek-ai/deepseek-v4-flash` (priority 3, role="fallback_only")
  - `nv-kimi` -> `moonshotai/kimi-k2.6` (priority 4, role="fallback_only")
  - `nv-nemotron` -> `nvidia/llama-3.1-nemotron-nano-8b-v1` (priority 5, role="fallback_only")

### 2. 账号与配额管理 (Quota Registry)
- [x] Google AI Studio (`gemini`): 支持读取 `auth.json` 里的多 Key 凭证池（进行 active keys 连通性校验）或环境变量作为可用判断，不参与主力高优先级路由。
- [x] NVIDIA NIM (`nvidia_nim`): 支持 synthetic 虚拟配额并结合报错进行 error cooldown 冷却（支持 429 冷却 1800 秒，401/403 永久拉黑）。

### 3. 选择器与 Fallback Chain 整合 (Model Selector)
- [x] 在主力模型全数耗尽或不可用时，系统优先选择 Google AI Studio 兜底模型（`gemini-fallback` / `gemini-pro-fallback`），再次则顺延 fallback 到 NVIDIA NIM 模型。
- [x] 支持在 fallback chain 链尾统一合并上述 fallback-only 候选队列。

### 4. 熔断与单任务保护 (Attempt Limit Guards)
- [x] 两个兜底 Provider 均限制单次任务总尝试次数不超过 2 次，且单次任务中同一个 alias 最多尝试 1 次，防止由于免费 API 不稳定导致的任务挂死或死循环。

### 5. Telegram 交互提示与端口绑定
- [x] 当触发 Google AI Studio 或 NVIDIA NIM 兜底时，Telegram 消息前缀会自动 prepend 轻量参考提示；若全部兜底亦均不可用，将输出友好提示。
- [x] 统一提供运行于 `8787` 端口的自定义接口服务，对 `/health`, `/quota`, `/model` 进行监控暴露。


## v3 设计与落地 (Volcengine / Ark Coding Plan 主力路由)

> Target: v3.0.0
> Status: Completed

### 1. 核心变化
- 将火山引擎 Ark Coding Plan (`provider: volcengine-coding-plan`, `model: ark-code-latest`) 纳入主力路由组。
- 新增 route group：`volcengine`，路由顺序调整为 `codex -> volcengine -> gemini -> claude -> gpt -> fallback_free`。
- 在模型注册表中增加可执行别名：`volcengine-ark-code-latest`，用于 selector、fallback chain 与 cron 默认模型选择。

### 2. 默认执行策略
- 系统默认模型固定为 `volcengine-coding-plan / ark-code-latest`。
- `model_selection.selected_model_alias` 固定为 `volcengine-ark-code-latest`，让未显式 pin 的 cron 任务走同一套 selector 路径。
- `fallback_providers` 第一跳保留火山模型，第二跳保留 Gemini API 兜底，避免旧 `volcano` 占位 endpoint 再次进入执行链路。

### 3. Cron 可用性处理
- 近期日志显示多个 `antigravity-acp` cron 任务反复出现 `Antigravity CLI failed. Code: 0`，导致重试耗尽。
- v3 将所有显式 pin 到 `antigravity-acp` 的启用 cron 任务迁移到 `volcengine-coding-plan / ark-code-latest`。
- 仍显式 pin 到 `openai-codex / gpt-5.4` 的系统维护、黄金与配额任务暂时保留，避免不必要地改动已可执行的高价值任务。

### 4. 验证要求
- `hermes chat -q '只回复 OK' --provider volcengine-coding-plan --model ark-code-latest --quiet` 必须返回 `OK`。
- `model_selector` 测试必须证明：Codex 不可用时会优先落到 `volcengine-ark-code-latest`，再进入 Gemini / Claude / GPT / fallback_free。
- Gateway 重启后日志中不应再出现旧 `provider=volcano` 或 `InvalidEndpointOrModel`。


## 2026-06-10 Volcengine family 双 Plan 设计

Volcengine 作为一个逻辑 family，但执行通道和 quota gate 分开：

- `family = volcengine`：用于 UI 分组、route group、任务画像路由。
- `provider = volcengine-agent-plan`：通用 Agent/Telegram/cron 默认通道，base_url 为 `/api/plan/v3`。
- `provider = volcengine-coding-plan`：代码/Review 优先通道，base_url 为 `/api/coding/v3`。
- `quota_family = volcengine_agent_plan` / `volcengine_coding_plan`：额度门控分开，避免 coding-plan 429 时误伤 agent-plan。

为避免两个 plan 下 raw model id 同名，所有 registry alias 都带 plan 前缀：

- `volcengine-agent-ark-code-latest`
- `volcengine-coding-ark-code-latest`
- `volcengine-agent-kimi-k2-6`
- `volcengine-coding-kimi-k2-6`

解析规则：

- `/model ark-code-latest` 因跨 plan 同名，应提示 ambiguous。
- `/model volcengine-agent-ark-code-latest` 明确选择 agent-plan。
- `/model volcengine-coding-ark-code-latest` 明确选择 coding-plan。
- `/model volcengine-agent-plan/ark-code-latest` 支持 provider/model 形式。

任务画像策略：

- `chat` / `cron` / `summary` / `light`：优先 `volcengine-agent-plan`。
- `code` / `review`：Codex 可用时仍优先 Codex；Codex 不可用时优先 `volcengine-coding-plan`。
- `analysis`：仍按全局画像优先级，Volcengine 内部按 task tags 和 alias priority 选择。

验证结果：

- `python3 -m py_compile agent/model_registry.py agent/model_selector.py agent/quota_registry.py agent/model_command.py` 通过。
- `pytest tests/agent/test_model_selector_priority.py tests/agent/test_model_command.py tests/agent/test_quota_registry.py -q`：19 passed。
- `hermes chat -q '只回复 OK' --quiet`：返回 OK。
- `hermes gateway restart`：成功，gateway PID 1888323。

## 2026-06-10 v3.2 更新：Telegram `/model` 简化与路由复核

### 1. Telegram `/model` 默认界面

默认 `/model` 不再输出完整 registry 明细，改为三段式短菜单：

1. **当前模型**：短名称 + alias + 当前 quota badge。
2. **推荐按钮**：展示自动路由在 `chat`、`code`、`analysis` 画像下推导出的少量高频候选，并补足常用兜底候选。
3. **额度概览**：每个 family 只显示核心额度状态；Volcengine 和 fallback_free 因内部有多个 quota_family，按子 quota_family 展开一行。

完整诊断仍保留在 `/model list` / `/model inspect`，当前模型详情保留在 `/model current`。这样 Telegram 默认菜单用于“快速选择”，完整清单用于“排障”。

### 2. Telegram 按钮命名

按钮从完整 alias 改为短标签：

- 当前模型用 `✓` 标记。
- 其他模型使用 family 短前缀，如 `Volc Ark Agent`、`Biz Mini`、`Gemini Low`。
- callback_data 仍使用完整 alias，因此显示简化不影响实际切换准确性，也不触碰 Telegram 64-byte callback 限制。

### 3. 切换确认消息

`/model <alias>` 或按钮切换后的确认消息改为中文短格式：

- 已切换的短名称
- alias
- 后端 provider / raw model
- quota + 当前会话/全局范围
- 必要时显示 context 或 CODEX_HOME

去掉旧版多行英文表格式输出，避免 Telegram 中出现冗余字段影响判断。

### 4. 路由复核结论

当前整体路由仍保持“任务画像优先 + family 内 priority 微调”：

- `chat` / `background` / `cron` / `summary` / `light`：`volcengine → codex → gemini → claude → gpt → fallback_free`。
- `code` / `review`：`codex → volcengine → claude → gemini → gpt → fallback_free`。
- `analysis`：`codex → claude → volcengine → gemini → gpt → fallback_free`。

Volcengine 仍采用双 Plan 物理隔离：

- `volcengine-agent-plan`：日常 chat、cron、summary、light 默认通道。
- `volcengine-coding-plan`：Codex 不可用或路由降级时的 code/review 通道。
- 两者使用独立 `quota_family`：`volcengine_agent_plan` 与 `volcengine_coding_plan`。
- 同 raw model id 继续使用 plan-prefixed alias，避免 `/model ark-code-latest` 误切换。

### 5. 验证结果

- `python3 -m py_compile agent/model_command.py gateway/platforms/telegram.py agent/model_selector.py agent/model_registry.py` 通过。
- `UV_CACHE_DIR=/tmp/hermes-uv-cache uv run --extra dev pytest tests/agent/test_model_command.py tests/agent/test_model_selector_priority.py tests/agent/test_quota_registry.py -q`：21 passed。
- 默认 `/model` 文本 smoke check：约 551 字符，包含当前模型、推荐按钮、额度概览；不再包含 `Quota 5h` 明细块和 `CODEX_HOME` 调试字段。
- 切换确认 smoke check：约 148 字符，中文短格式。


## 2026-06-10 v3.3 总体系统设计：简洁、清晰、高可用、高 quota 利用率

### 1. 总体架构分层

模型系统固定拆成五层，避免把配置、选择、展示和故障处理混在一起：

1. **Registry 层**
   - 只定义“有哪些可执行模型”。
   - 每个条目必须包含唯一 alias、family、provider、raw model id、quota_family、task tags、priority、context limit、supports_execute。
   - raw model id 可以重复，但 alias 不允许重复。

2. **Quota / Health 层**
   - 只回答“这个 slot 现在能不能用”。
   - quota、认证状态、最近 401/403/429/5xx/timeout、reset 时间和 cooldown 都在这里归一化。
   - `N/A` 不当作可用额度；在自动选择中按不可用/耗尽处理，除非未来有更高可信来源明确证明可用。

3. **Selector 层**
   - 只负责“为这个 task 选哪个 alias”。
   - 输入：task、显式偏好、registry、quota、health、session sticky。
   - 输出：选中的 alias、fallback chain、跳过原因。
   - 不直接拼 UI 文案，不直接读取 Telegram callback。

4. **Execution 层**
   - 只负责“按 alias 精确执行”。
   - provider/base_url/api_key/ACP command/CODEX_HOME 等 runtime bundle 必须跟随 alias 完整传递。
   - 失败结果必须回写 health/cooldown，供下一次 selector 避免重复踩坑。

5. **UI / Command 层**
   - 只负责“让用户看懂并快速切换”。
   - 默认 `/model` 是快捷面板，不是诊断报告。
   - 全量排障进入 `/model list`、`/model inspect`、日志和测试命令。

### 2. 命名与可见性规则

为避免混乱，系统只允许三类名字各司其职：

- **alias**：唯一、稳定、可持久化，例如 `volcengine-agent-ark-code-latest`。
- **display label**：短、人类可读，例如 `Volc Ark Agent`。
- **raw model id**：上游真实模型名，例如 `ark-code-latest`。

规则：

- 配置、callback、session override、cron pin 一律保存 alias 或明确的 provider/model，不保存短标签。
- Telegram 按钮可以短，但 callback_data 必须是完整 alias。
- 同 raw model id 在多个 plan/provider 下出现时，用户输入 raw id 必须提示歧义，而不是自动猜。
- 任何 fallback-only 或实验模型默认不进入高频按钮区，只进入 `/model list`。

### 3. 高可用设计

高可用不是靠无限 fallback，而是靠“短链路 + 快速熔断 + 可解释降级”：

1. **硬门槛先过滤**
   - provider 未认证、quota 0/N/A、近期熔断、supports_execute=false 的候选直接跳过。

2. **同组优先，跨组有序**
   - 先在当前 task 对应的 route group 内选择健康 slot。
   - 同组不可用时再跨到下一 route group。
   - ACP 类 provider 避免 AGY/Codex/AGY 交错，减少 subprocess reset 和超时。

3. **失败写回健康状态**
   - 401/403：认证失败，长冷却或禁用。
   - 429/quota_exhausted：等 reset 或降权。
   - 5xx/timeout/empty response：短冷却，避免同任务重复打。

4. **cron 单独保守**
   - cron 任务以成功率和可预测性优先。
   - 未显式 pin 的 cron 走 `cron/summary/light` 画像。
   - 显式 pin 的 cron 默认不被 selector 覆盖，除非配置明确允许。
   - 单次 cron 的 fallback chain 保持短，宁可给出清晰错误摘要，也不要挂死。

### 4. quota 利用率设计

quota 利用率的目标是“该用的用满，不该烧的不烧”：

1. **按任务价值分配额度**
   - `chat/background/cron/summary/light`：优先 Volcengine agent-plan 等稳定低耗通道。
   - `code/review`：优先 Codex；Codex 不健康时切 Volcengine coding-plan。
   - `analysis`：优先 Codex/Claude 等强推理通道，但仍受 quota/health 门控。

2. **同能力组内做 slot 轮转**
   - 多账号/多 key 情况下，先在同能力组内平衡消耗。
   - 不因单个账号额度低就立刻升级到更贵模型。

3. **保留优质额度**
   - 低价值高频任务不默认消耗 Codex/强推理额度。
   - 用户显式 `/model` 选择时尊重用户偏好，但 quota 耗尽时必须解释降级。

4. **额度状态可观测**
   - `/model` 默认只显示 family 级健康摘要。
   - `/model inspect` 显示每个 alias/slot 的 quota、health、cooldown、skip reason。
   - quota 监控任务负责中文归因报告，避免用户从原始 JSON 里猜。

### 5. 默认推荐策略

当前推荐保持“少而准”：

- 默认交互：`volcengine-agent-ark-code-latest`
- 编程/Review：Codex 健康时优先 Codex，否则 `volcengine-coding-ark-code-latest`
- 深度分析：Codex / Claude / Volcengine 按画像和 quota 选择
- 兜底：Gemini API / fallback_free 只在主力不可用时进入，不抢默认入口

Telegram `/model` 默认只展示这些高频候选；完整模型池不在默认界面展开。

### 6. 验收标准

每次模型系统改动完成后，必须至少验证：

1. **简洁**：Telegram `/model` 默认输出不回退到大段 registry 明细；切换确认为中文短消息。
2. **清晰**：同 raw model 跨 plan/provider 的输入会提示歧义；plan-prefixed alias 能精确解析。
3. **高可用**：主路径 smoke test 返回正常；模拟 quota/auth 不可用时 selector 能给出 fallback reason。
4. **高 quota 利用率**：低价值任务不会默认烧 Codex；code/review 在 Codex 健康时仍能优先使用强模型。
5. **可审计**：测试、配置、gateway 日志能对应到同一个 alias/provider/raw model，不出现“UI 显示 A，实际跑 B”的状态漂移。

## 2026-06-11 v3.4 系统核心路由策略梳理与细化

本章节对系统内 **付费模型大类**（Antigravity 双付费账号、OpenAI Codex 双 Slot、Volcengine 双 Plan）与 **备用/兜底模型大类**（Gemini Multi-Key、NVIDIA NIM Free Fallback、Nous Portal）的物理配置、路由调度及动态冷却机制进行系统性梳理。

---

### 一、 付费模型通道（Primary Layer）

付费模型是系统的核心执行通道，在路由打分中具有最高优先级，拥有完备的账号隔离与动态限流规避机制。

#### 1. Dual agy (双付费账号) 物理隔离与动态 Cooldown 桥接设计
在 ACP (`antigravity-acp`) 物理通道中，系统通过 `bin/agy_acp_bridge.py` 实现了两个付费 Google Antigravity 账号的热切换：
*   **物理 HOME 切换隔离**：
    *   `primary` 账号：绑定 `/home/lighthouse` 目录。
    *   `secondary` 账号：绑定 `/home/lighthouse/.hermes/second_home` 目录。
    *   桥接器在调用底层 `agy` CLI 子进程时，通过覆写 `HOME` 环境变量实现两个账号的 Token 凭证、配置以及本地 cache 目录的物理隔离。
*   **主备智能路由调度**：
    *   桥接器在内存中维护 `rate_limit_until` 物理冷却时间戳。
    *   执行时，若 primary 账号未冷却，则默认路由到 primary 运行。
    *   若 primary 账号冷却，且检测到 secondary 账号目录下存在有效 Token（`antigravity-oauth-token`），则子进程执行环境自动热切到 `secondary`，保证任务不间断。
*   **自适应冷却时长计算**：
    *   执行返回后，若子进程报错，桥接器会正则解析输出的错误信息：
        1.  **短周期速率限制**：如检测到 `retry in X.XXs`，计算 `X + 5s` 作为冷却期限，精确覆盖限流，防并发激进重试导致被上游封禁。
        2.  **长周期配额超限**：如检测到 `usage limit`、`quota` 或 `RESOURCE_EXHAUSTED`，直接将该账号冷却 3600 秒 (1 小时)。
        3.  **常规超时或空响应**：冷却 300 秒 (5 分钟)，触发下一跳或账号切换。

#### 2. OpenAI Codex 双账户槽位 (Slot) 主动额度调度
为了最优化调度多套 Codex 付费额度，系统针对 `openai-codex` provider 实现了双账户槽位支持：
*   **双 Slot 物理隔离**：
    *   `codex_plus`：对应 `CODEX_HOME` = `/home/lighthouse/.codex-plus`（对应 `plus` 账户）。
    *   `codex_business`：对应 `CODEX_HOME` = `/home/lighthouse/.codex-business`（对应 `business` 账户）。
    *   在 Model Registry 中将 `codex_home` 与 `codex_account` 属性显式绑定至对应的 `ModelEntry`，迫使 Selector 对其做唯一性识别，防止同名 raw model 发生去重漂移。
*   **基于 cclimits 的主动额度查询**：
    *   `quota_registry.py` 通过动态切换 `CODEX_HOME` 运行 `cclimits` CLI 命令。
    *   精准提取 `OpenAI Codex` 字段，获取其 `5h` 和 `7d` 滚动周期下的剩余额度百分比及重置时间。
*   **多维打分与“额度优先”动态分流 (Scoring Algorithm)**：
    *   Selector 选用 `_candidate_score` 方法对候选模型打分排序：
        $$\text{Score} = (1, \text{Quota Bucket}, \text{Quota Percent}, \text{Task Fit}, \text{Group Priority}, -\text{Cost}, \text{Family Priority}, \text{Entry Priority})$$
    *   打分元组的前两位为剩余额度所在的 Bucket (分 50% 以上、20% 以上、1% 以上、0%) 和具体百分比。
    *   **健康额度主导**：若某 Codex 账户（如 Plus）的额度因高频消耗降入 Warning 或 Low 档（低于 20%），其打分将大幅降低。**Selector 会自动将任务路由到另一个额度健康的 Codex 账户（如 Business），或直接分流分派给健康的 Volcengine Coding Plan**，从而实现对 Codex 贵重额度的动态避险和保护。

#### 3. Volcengine (火山引擎) 双 Plan 隔离与日志冷却机制
Volcengine 作为一个逻辑大类，在底层物理设计上拆分为了两个独立的 Plan 通道：
*   **Plan 物理隔离**：
    *   `volcengine-agent-plan` (`quota_family: volcengine_agent_plan`)：绑定 `/api/plan/v3` 端点，负责日常通用交互、Telegram `/model` 菜单、Cron 定时任务以及轻量 summary。
    *   `volcengine-coding-plan` (`quota_family: volcengine_coding_plan`)：绑定 `/api/coding/v3` 端点，负责代码生成、深度 Review 及分析任务。
*   **基于日志解析的动态冷却 (Log-based Cooldown)**：
    *   由于火山引擎不支持主动的额度查询 API，系统通过主动错误捕获实现冷却。
    *   在进行 Quota 检查时，`quota_registry.py` 的 `_check_volcengine_cooldown()` 方法会扫描 `/home/lighthouse/.hermes/logs/errors.log` 的尾部区域 (256KB)。
    *   一旦检测到 `429` 或 `quota_exceeded` / `AccountQuotaExceeded` 错误，系统会正则捕获行内的 `reset at YYYY-MM-DD HH:MM:SS` 绝对重置时间。
    *   在未到重置时间前，该 Plan 状态会被置为 `quota_exhausted`，Selector 会自动跳过并执行 Fallback，防止对其产生无效的重复请求。

---

### 二、 备用与兜底模型通道 (Fallback & Aggregator Layer)

备用和兜底通道拥有更低的路由打分，仅在所有主力付费通道额度耗尽或暂时不可用时，承接系统的接力运行。

#### 4. Gemini Multi-Key 负载均衡与限流规避
Gemini (Google AI Studio) 是系统最重要的高频备用层，其凭证池拥有多 Key 轮转能力：
*   **凭证池多密钥配置**：
    *   在 `/home/lighthouse/.hermes/auth.json` 的 `credential_pool.gemini` 中配置了 4 个绝对隔离的 API Key (`GOOGLE_API_KEY`, `AI Studio Fallback 2`, `cmliuxiaolei`, `dirisephan`)，同享 `priority: 1` 权重。
*   **最少并发软租约负载均衡 (Least Active Leases)**：
    *   为了防止触发 AI Studio 免费层/低速层严格的 RPM/TPM 限流，凭证池管理器 `credential_pool.py` 在 `acquire_lease()` 阶段引入了软租约策略：
        $$\text{chosen} = \min(\text{candidates}, \text{key} = (\text{active\_leases}, \text{priority}))$$
    *   并发请求会优先分配给**当前正在处理请求数最少**且未冷却的 Key。当有 2 个并发任务时，系统会分别分派 2 个不同的物理 Key，实现在底层的负载均衡。
*   **单 Key 限流隔离 (Dynamic Cooldown Bypass)**：
    *   任何 Key 在请求中遭遇 `429 (RESOURCE_EXHAUSTED)` 时，系统调用 `_mark_exhausted()` 将其标为 `exhausted`。
    *   若响应中包含重试延迟时间戳，则精准冷却到重置点；若无，则施加系统默认的 **1 小时** (`EXHAUSTED_TTL_429_SECONDS = 3600`) 物理冷却期。
    *   冷却期内，该 Key 不会进入 `_available_entries()` 返回的备选池。整个 Gemini 通道将自动绕过限流的 Key，在剩下 3 个健康 Key 中轮转，杜绝单 Key 限流破坏整体兜底通道的可用性。

#### 5. NVIDIA NIM Free Fallback 兜底与健康检查
NVIDIA NIM 属于最低优先级的 `fallback_free` 路由组，作为主力与 Gemini 均失效后的最后一跳保障：
*   **模型注册与降级序列**：
    *   注册了 3 个 fallback-only 别名，严格按照优先级进行链尾尝试：
        `nv-fallback` (deepseek-v4-flash, 优先) $\rightarrow$ `nv-kimi` (kimi-k2.6) $\rightarrow$ `nv-nemotron` (nemotron-nano-8b)。
*   **双层尝试上限保护**：
    *   单次任务中，对 `nvidia_nim` 施加了**总尝试不超过 2 次**的限制，且**同一个 model alias 最多只允许调用一次**，防止在单一故障端点上反复死锁。
*   **智能 Cooldown 状态回写**：
    *   `error_classifier.py` 会捕获 NVIDIA NIM 的错误状态，并写入 `/home/lighthouse/.hermes/nvidia_state.json`：
        *   **401 / 403 (Auth)**：认定 Key 无效，冷却 = 0，拉黑禁用 (`execute_enabled = False`)。
        *   **402 (Payment/Credits)**：欠费或超限，冷却 24 小时 (86400 秒)。
        *   **429 (Rate Limit)**：限流，冷却 30 分钟 (1800 秒)。
        *   **5xx / Timeout / Others**：服务抖动，冷却 15 分钟 (900 秒)。
*   **主动健康检查 (Active Health Check)**：
    *   `quota_registry.py` 通过 `get_nvidia_nim_status()` 维护一个 5 分钟的缓存。缓存过期后，会自动向 NIM 端点发起轻量级 `"ping"` 请求，一旦握手成功将解除非永久性的冷却状态，使其快速恢复。

#### 6. Nous Research Portal 可用性与登录引导
*   **可用性评估**：**未配置，当前不可用**。
*   **机制与鉴权**：
    *   Nous Portal 是内置的 Proxy/Aggregator 通道，能够兼容转发 OpenRouter/Nous 格式。并且它作为“托管工具网关 (Managed Tool Gateway)”，承担了 Web 搜索、FAL 图像生成、TTS、Browser 使用等网关工具的鉴权。
    *   目前 `auth.json` 中无任何 `nous` 凭证，因此无法被 Selector 激活。若要启用，用户需要运行 `hermes auth add nous --type oauth` 或者使用 `hermes portal` 快捷指令以通过 Device-code OAuth 完成登录授权。
*   **Nous 专属断路器 (Breaker Guard)**：
    *   `nous_rate_guard.py` 会监测 Nous 接口的 `x-ratelimit-remaining` 响应头。
    *   仅在判定确实是**用户账户级 tap 耗尽**（RPH 耗尽，且 reset 窗口 $\ge 60$ 秒）时，才会向 `/home/lighthouse/.hermes/rate_limits/nous.json` 写入长周期全局熔断；对于上游单模型抖动则不触发断路器，以防 DeepSeek 抖动误伤 Kimi 或 MiMo 等其他模型。

## 2026-06-11 v3.8 执行身份、AGY Slot 与消息输出契约

### 1. 三层模型身份

所有入口必须区分以下三个身份，禁止再用单一 `model` 字段混合表达：

1. **requested**：用户或配置显式偏好的 alias。
2. **selected**：Selector 根据 quota、health 和任务画像选出的 RoutePlan 主路由。
3. **executed**：本次请求最终成功执行的 provider/model；AGY 还必须包含实际 HOME slot。

`/model current` 负责解释 requested 与 selected；消息 footer 和 CLI 状态栏优先显示 executed。发生 fallback 时必须让 requested 与 executed 同时可追踪。

### 2. AGY 双账号执行契约

- quota account 与物理 HOME slot 是两个不同概念，不得把 quota 聚合结果当作实际执行账号。
- `primary=/home/lighthouse`、`secondary=/home/lighthouse/.hermes/second_home` 的认证和冷却状态写入 `/home/lighthouse/.hermes/state/agy_slots.json`。
- slot 冷却必须跨 `agy_acp_bridge.py` 进程持久化；新的 ACP 请求不得遗忘上一次 429 后重新撞击同一账号。
- 任一健康 slot 可执行时 AGY family 可用；全部已认证 slot 冷却时，Selector 将 AGY 作为硬门槛失败并进入下一路由。
- bridge 成功后写入 `last_executed_slot`；footer/CLI 使用该状态展示如 `gemini-low@secondary` 的真实执行身份。

### 3. 中间过程与完成状态契约

- Telegram 默认不发送 interim assistant message；只保留必要的工具进度或明确状态通知。
- AGY bridge 过滤 “I will search/list/view/read/run/wait...” 等过程旁白，避免其成为用户可见正文。
- 只有过程旁白、没有有效最终答案的响应必须判定为失败，允许 fallback 接管；禁止把“仍在处理”误报为任务完成。
- 每个任务最终必须有明确终态：有效答案、清晰失败摘要或可追踪的后台任务状态。

### 4. `/model` 与 `/model list` 契约

- `/model` 仅显示当前偏好、最多五个可用候选和 family 额度摘要。
- 候选 badge 使用具体模型有效 quota，不使用可能与模型不一致的 family 聚合 badge。
- 候选按可用性和有效 quota 倒序；同 quota 时主力付费/订阅 route group 优先，`fallback_free` 靠后。
- `/model list` 使用单条中文清单，全局按有效 quota 倒序；不得回退到英文 registry dump、内部 reason 值或 Telegram 多段刷屏。
- 未知但可用的额度显示“可用”；未知且不可用的状态显示“状态未知/不可用”，不得伪造百分比。

## 2026-06-11 v3.9 任务生命周期可靠性契约

### 1. 终态必须有有效结果

- `completed` 只允许用于包含实际结果、明确结论或可验证产出的响应。
- 仅包含 `I will...`、准备执行、等待、计划或工具意图的文本属于 narration-only，不得作为成功终态。
- narration-only 响应先在当前路由做一次有界 continuation；仍无结果则进入下一 RoutePlan，最终必须明确 `failed`。

### 2. 输入不能隐式破坏进行中的任务

- Telegram 普通文本默认排队，在当前任务完成后形成下一 turn。
- `/steer` 用于改变当前任务方向；`/stop` 和 `/new` 是显式中断入口。
- 只读控制面命令在忙碌期可执行；任何会改变当前执行路由的命令只能在 turn 边界生效。

### 3. 进度必须可解释

运行态至少暴露：`phase`、真实执行 route、route attempt、当前工具、最后有效活动、运行时长。阶段集合统一为：

`routing / compression / model_wait / tool / fallback / finalizing / completed / failed / interrupted`

心跳只编辑同一条状态消息，不发送模型过程旁白；停滞告警必须指出当前阶段与最后活动。

### 4. 停滞按阶段处理

- 模型首包、ACP subprocess 无输出、工具执行和整体 inactivity 使用不同预算。
- 能安全重试或切路由的阶段优先 fallback；不能安全恢复时明确失败，禁止无限等待。
- 全局 15/30 分钟 watchdog 仅作为最后保护，不替代阶段级诊断。

### 5. Auxiliary 服从 RoutePlan

- compression、title generation、web extract 等辅助任务使用对应 task 画像的共享 RoutePlan。
- 显式 auxiliary provider 的 429、503、529、quota/capacity、连接错误必须允许跨 provider fallback。
- 付费/订阅健康路由优先于 `fallback_free`；静态 compression marker 是所有模型路线失败后的最终保护。
