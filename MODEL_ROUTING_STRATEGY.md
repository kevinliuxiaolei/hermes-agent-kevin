# 模型路由与选择策略 (v3.10)

> Version: 3.10.0
> 目标：用一套统一、可解释、可验证的模型路由系统，同时达成简洁、清晰、可用率高、quota 利用率高。用户界面只暴露少量高频选择；底层保留完整 registry、quota、health、fallback 与审计能力。

## 1. 路由分层

系统按四层组织：

1. `registry`：模型清单与静态元数据的单一事实来源。
2. `quota`：把显示额度、可执行额度和物理账号状态拆开管理。
3. `health`：记录认证失败、429、5xx、timeout 等运行时故障，阻止反复撞坏路由。
4. `route_plan`：把显式偏好、健康门禁和 registry 候选合并成有界执行链。

## 2. 版本里程碑

- v3.5：把 Cron、Gateway、CLI、Auxiliary 全部统一到 RoutePlan。
- v3.6：把健康状态、quota 和尝试预算统一成硬门禁。
- v3.7：把打分、Doctor 和真实故障验收收敛到同一套解释。
- v3.8：区分 `requested`、`selected`、`executed` 三层身份，并把 AGY 从单一 family 改成双 HOME slot。
- v3.9：把任务生命周期、终态、进度和停滞诊断纳入路由契约。
- v3.10：补齐认证门禁、Telegram picker、`custom` provider 归一化、fallback 可观测性。

## 3. 核心路由原则

### 3.1 显式路由

- 显式 alias 在 runtime 和 quota 可用时保持为 primary。
- 如果 primary 失效，进入 RoutePlan fallback。
- `strict_pin` 是唯一的硬固定模式，失败就停止。

### 3.2 自动选择

- 自动选择先遵守 task route group。
- 同组内按任务适配、quota、成本、priority 排序。
- `fallback_free` 只在付费/订阅路线失败后作为最后一层保障。

### 3.3 健康门禁

- 401 / 403 认证失败不是“冷却后自动恢复”问题，而是“凭证已经不可信”问题。
- 429 / quota exhausted / server error / timeout 必须写入共享健康状态。
- 成功执行会清除健康状态，但认证失败的恢复必须显式触发。

### 3.4 路由唯一性

- provider / model / CODEX slot 相同的路由必须去重。
- `current` primary 必须从 fallback 中移除。
- 配置 fallback 只表示偏好顺序，不表示完整执行链。

## 4. 三层身份

所有入口都要区分：

- `requested`：用户或配置的显式偏好。
- `selected`：Selector 根据 quota 和 health 选出的主路由。
- `executed`：本次请求最终成功执行的 provider/model；AGY 还要带实际 HOME slot。

UI 上：

- `/model current` 解释 `requested` 和 `selected`。
- 消息 footer 和 CLI 状态栏优先显示 `executed`。
- 发生 fallback 时，`requested` 和 `executed` 都必须能追踪。

## 5. AGY 双 Slot 契约

- quota family 不是执行账号。
- `primary=/home/lighthouse`、`secondary=/home/lighthouse/.hermes/second_home` 视为两个物理隔离 HOME slot。
- slot 冷却写入持久状态，不能在 bridge 重启后遗忘。
- 任一健康 slot 可用时，AGY family 可执行。
- 全部认证 slot 冷却时，AGY 必须被健康门禁挡下。

## 6. Telegram / CLI 展示契约

- `/model` 只显示当前偏好、少量可用候选和 family 额度概览。
- `/model list` 显示全局按有效 quota 倒序的中文清单。
- `/model list all` 保留完整 registry 诊断视图。
- picker 按钮必须复用同一套 quota/health 排序，不能和文本菜单分裂。
- 消息 footer 必须写清楚真实执行模型，不再只显示模糊的“当前模型”。

## 7. 生命周期与中间过程

- narration-only 内容不能算成功终态。
- Telegram 默认不刷屏输出中间过程；只保留必要进度或明确状态通知。
- 阶段级状态至少要有：routing / compression / model_wait / tool / fallback / finalizing / completed / failed / interrupted。
- 任务打断后必须给出明确的中断状态，而不是静默结束。

## 8. Auxiliary 任务

- compression、title generation、web extract 服从同一套 RoutePlan。
- 显式 auxiliary provider 遇到 quota / capacity / connection 问题时允许跨 provider fallback。
- 压缩失败要记录候选、跳过原因和耗尽信息，方便判断是路由问题还是模型问题。

## 9. 当前设计状态

这套策略在 v3.10 的目标是把“看起来可用”收敛成“真的可执行、可恢复、可解释”。

当前重点不是继续扩展更多模型，而是：

1. 保证 selector、执行层、footer 和 picker 使用同一套 truth。
2. 保证认证失败、quota 失败和 provider 失效不会反复打同一条坏路径。
3. 保证用户看到的消息足够短，但足以定位任务到底停在哪一层。
