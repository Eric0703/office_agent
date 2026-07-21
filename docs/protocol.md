# AI 工牌 — 协议登记册(Protocol Registry)

| 项 | 内容 |
|---|---|
| 版本 | v1.0(冻结候选,Gate 0 评审后生效) |
| 效力 | 端-云协议的**唯一事实来源**(宪法第 6 条、规约 §3)。任何消息的新增/修改必须先改本文件 |
| 读者 | Agent 主机开发、虚拟工牌开发、后续硬件固件开发 |

---

## 1. 总则

### 1.1 传输

| 通道 | 用途 | 演示期(虚拟工牌) | 硬件期 |
|---|---|---|---|
| 控制通道 | 全部 JSON 消息 | WebSocket `/ws` | BLE GATT(映射设计见 A3) |
| 音频通道 | 音频文件上传 | HTTP POST `/audio/{record_id}` | Wi-Fi HTTP(同左) |

### 1.2 消息信封

所有控制通道消息为 JSON 对象,统一信封:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | string | 是 | 消息类型,点分命名,如 `record.start` |
| `version` | string | 是 | 协议版本,当前 `"1.0"` |
| `id` | string(uuid) | 是 | 本条消息唯一 id |
| `ts` | int64 | 是 | 发送方 Unix 毫秒时间戳 |
| `payload` | object | 是 | 消息体,见各消息定义(可为空对象 `{}`) |

### 1.3 兼容规则(规约 §3)

- 接收方**必须忽略未知字段**;新字段只可新增,旧字段不删除、只可标记 `deprecated`。
- 不兼容变更必须升级 `version` 大版本,且主机需同时支持相邻两个大版本。
- 所有请求类消息都有对应的 `ack`/`error` 或专用结果消息;以信封 `id` 关联(`ref_id`)。

### 1.4 方向约定

`D→H`:设备(工牌/虚拟工牌)到主机;`H→D`:主机到设备;`CLI`:主机本地命令行操作。

---

## 2. 消息定义

### 2.1 连接与配对

#### `device.hello` (D→H) — 连接建立后的认证

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `device_id` | string | 是 | 配对时分配的 id;未配对设备填空字符串 `""` |
| `token` | string | 是 | 配对 token;未配对为空字符串 |
| `client` | string | 是 | 客户端标识:`vbadge-web` / `badge-fw` / `headless-test` |
| `client_version` | string | 是 | 客户端版本 |
| `display_profile` | string | 是 | 屏幕模组档位:`400x300`(4.2")/ `296x128`(2.9")。模组不等于工牌外形;UI 一律 Portrait 竖向排版,仿真画布 A/B 统一 300×400(08 §6.3;296×128 仅为 2.9" 模组物理参数);主机按档截断推送内容 |

响应:`device.hello.result` (H→D)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | enum | 是 | `ok` / `pair_required` / `auth_failed` / `revoked` |
| `server_time` | int64 | 是 | 服务器时间,用于端侧校时 |
| `device_id` | string | 条件 | `ok` 时必填,回显端侧身份:A0 原型期(dev_mode=auto_approve)由端侧生成稳定 id、主机登记后回显;正式配对后回显已绑定 id。端侧必须持久化该值并用于音频上传的 `X-Device-Id`,**禁止空值上传** |

`status != ok` 时主机在发送结果后关闭连接。`ok` 后主机立即发 `state.sync`(§2.6)。

#### `device.pair.request` (D→H) — 请求配对

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `pair_code` | string(6 位数字) | 是 | 端侧生成并展示的配对码 |
| `device_name` | string | 是 | 设备显示名,如 "小王的工牌" |

主机收到后置为 pending,**Owner 在 CLI 执行 `agent-host pair approve <pair_code>` 后**,主机发送:

#### `device.pair.result` (H→D)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | enum | 是 | `approved` / `rejected` / `expired` |
| `device_id` | string | 条件 | approved 时必填 |
| `token` | string | 条件 | approved 时必填,端侧持久化 |

配对码有效期 5 分钟,一次性。

#### `device.revoke` (H→D) — 吊销通知

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `reason` | string | 否 | 展示用原因 |

设备收到后必须清除本地 token 与全部卡片,回到未配对页。CLI:`agent-host pair revoke <device_id>`。

#### `heartbeat` (双向)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| (payload 为空) | | | |

端→主 ping,主→端 pong 复用同一 `id`。30 秒间隔,连续 2 次未达即判定断连。

### 2.2 录音与上传

#### `record.start` (D→H)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `record_id` | string(uuid) | 是 | 端侧生成,幂等键 |
| `mode` | enum | 是 | `auto` / `field`(现场记录)/ `experience`(经验沉淀) |
| `started_at` | int64 | 是 | 按键时刻 |

主机响应 `ack`。弱网允许丢失——不上传音频的 start 无意义,主机以音频到达为准。

#### `record.stop` (D→H)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `record_id` | string(uuid) | 是 | |
| `duration_ms` | int | 是 | 录音时长;<2000 视为误触,端侧不上传音频 |

#### 音频上传(HTTP,非 WS)

`POST /audio/{record_id}`,Header:

| Header | 必填 | 说明 |
|---|---|---|
| `X-Device-Id` / `X-Token` | 是 | 认证,失败返回 401 |
| `X-Audio-Format` | 是 | `webm-opus` / `opus` / `wav` |
| `X-Duration-Ms` | 是 | 与 record.stop 一致 |

Body 为原始音频字节。响应 JSON:

| 字段 | 说明 |
|---|---|
| `status` | `received` / `duplicate`(幂等:同 record_id 重传返回首次受理结果) |
| `record_id` | 回显 |

大小上限 20MB(≈ 20 分钟 opus),超限 413。上传不完整(连接中断)由端侧整体重传,主机不做断点续传——补传语义在端侧缓存层(见 08 §1.1 OfflineCached)。

> **实现状态(2026-07-19,Gate 0)**:A0 原型期 `dev_mode=auto_approve` 下,主机**仅校验 `X-Device-Id` 头存在**;`X-Token` 的正式校验属于 A1 配对实现,**当前未完成,不得宣称已实现**。

### 2.3 意图与执行

#### `intent.result` (H→D) — 处理终态或中间态

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `record_id` | string | 是 | |
| `status` | enum | 是 | `success` / `failed` / `pending_confirm` / `clarify` / `low_confidence` |
| `title` | string | 是 | 端侧大标题,如 "已完成:周报撰写" |
| `body` | string | 否 | 补充说明 |
| `candidates` | array | 条件 | status=clarify 时必填:[{`candidate_id`, `label`}] ,≤5 条 |
| `error_code` | string | 条件 | failed 时必填,见 §3 |

#### `clarify.select` (D→H) — 歧义候选选择

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `record_id` | string | 是 | 对应 intent.result 的 record_id |
| `candidate_id` | string | 是 | |

选择后流程回到执行,结果仍走 `intent.result`。

#### `confirm.request` (H→D) — L2 风险确认

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `confirm_id` | string(uuid) | 是 | |
| `record_id` | string | 是 | 关联录音 |
| `title` | string | 是 | 后果文案,如 "将向客户A发送《XX方案 v3》" |
| `body` | string | 否 | 参数明细 |
| `timeout_s` | int | 是 | 默认 15 |

#### `confirm.response` (D→H)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `confirm_id` | string | 是 | |
| `decision` | enum | 是 | `confirm` / `cancel` |

超时主机自动按 cancel 处理并回 `intent.result{status: failed, error_code: CONFIRM_TIMEOUT}`。

### 2.4 卡片与简报

#### `brief.push` (H→D)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `briefing_id` | string | 是 | |
| `date` | string(YYYY-MM-DD) | 是 | |
| `items` | array | 是 | [{`kind`: `event`\|`task`\|`conflict`, `title`, `time` 可空, `source_id`}] ≤5 条,顺序即展示顺序 |

#### `reminder.push` (H→D)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `card` | object | 是 | {`card_id`, `kind`: `task`\|`timer`, `title`, `body` 可空, `remind_at` 可空} |

#### `reminder.dismiss` (H→D)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `card_id` | string | 是 | |
| `reason` | enum | 是 | `completed` / `cancelled` / `expired` |

#### `card.ack` (D→H)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `card_id` | string | 是 | 端侧确认已展示/已撤下 |
| `action` | enum | 是 | `displayed` / `dismissed` |

### 2.5 通用

#### `ack` (双向)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ref_id` | string | 是 | 被应答消息的信封 id |
| `status` | enum | 是 | `ok` / `error` |
| `error` | object | 条件 | {`code`, `message`} |

### 2.6 状态同步

#### `state.sync` (H→D) — 认证成功后或端侧请求时全量下发

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `cards` | array | 是 | 全部 active 卡片,结构同 reminder.push 的 card;可为空数组 |
| `briefing` | object | 否 | 当日简报,结构同 brief.push(无则省略) |
| `pending_confirm` | object | 否 | 未决确认请求,结构同 confirm.request(无则省略) |

#### `state.sync.request` (D→H) — payload 为空,端侧判断状态脏时主动请求

---

## 3. 错误码

| code | 含义 | 端侧行为 |
|---|---|---|
| `AUTH_FAILED` | token 无效 | 回未配对页 |
| `DEVICE_REVOKED` | 已吊销 | 清除数据回未配对页 |
| `ASR_FAILED` | 转写失败 | 提示重说 |
| `ASR_LOW_CONFIDENCE` | 转写置信度低 | 提示到安静处或 PC 补录 |
| `INTENT_UNKNOWN` | 未理解 | 提示换说法或到 PC 操作 |
| `TASK_AMBIGUOUS` | 任务歧义(伴随 clarify) | 展示候选 |
| `BACKEND_UNREACHABLE` | 办公系统不可达 | 提示稍后在 PC 重试 |
| `CONFIRM_TIMEOUT` | 确认超时自动取消 | 展示"已取消" |
| `RATE_LIMITED` | 触发过频 | 稍后再试 |
| `INTERNAL` | 未分类错误 | 提示并留日志 |

---

## 4. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.0(草案) | 2026-07-19 | 初版,Gate 0 评审项 |
| 1.0(草案修订) | 2026-07-19 | Gate 0:hello.result 增加 device_id 回显;标注音频上传认证实现状态(A0 仅校验 X-Device-Id);录音交互由"按住说话"改为单键切换(record.start/stop 语义不变) |
| 1.0(草案修订2) | 2026-07-20 | 硬件 v0.2 双方案(01 §5):device.hello 增加 `display_profile` 画布档位声明(400x300 / 296x128),显示契约改 A/B 两档(08 §6) |
| 1.0(草案修订3) | 2026-07-20 | Owner 决策:工牌外形 60×90mm 竖向,模组与外形分离;页面模型改"默认提醒页+手动简报页",录音页静态化;`display_profile` 语义更新为模组档位 + 竖向逻辑画布 |
| 1.0(草案修订4) | 2026-07-20 | Owner 决策:B 档仿真画布与 A 统一为 300×400 竖向(128×296 窄行宽多次折行,存在一屏显示不完风险);296×128 保留为 2.9" 模组物理参数与协议档位 |
