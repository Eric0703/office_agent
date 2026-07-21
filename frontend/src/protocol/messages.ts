/**
 * 端-云协议类型定义。
 * 唯一事实来源:docs/protocol.md(宪法第 6 条、规约 §3)——任何消息的新增/修改必须先改登记册。
 * 方向约定(登记册 §1.4):D→H 设备到主机;H→D 主机到设备。
 */

/** 协议版本(登记册 §1.2) */
export const PROTOCOL_VERSION = "1.0";

/** 消息信封(登记册 §1.2):ts 为发送方 Unix 毫秒时间戳 */
export interface Envelope<T extends string, P> {
  type: T;
  version: string;
  id: string;
  ts: number;
  payload: P;
}

/* ---------- 2.1 连接与配对 ---------- */

/** 客户端标识 */
export type ClientKind = "vbadge-web" | "badge-fw" | "headless-test";

/** 显示画布档位(登记册 §2.1;08 §6 显示契约):屏幕模组档位 400x300(4.2" 模组)/
 * 296x128(2.9" 模组);模组横排参数仅标识硬件,仿真画布 A/B 统一 300×400 竖向 */
export type DisplayProfile = "400x300" | "296x128";

/** device.hello (D→H) — 连接建立后的认证;未配对设备 device_id/token 填空字符串 */
export interface DeviceHelloPayload {
  device_id: string;
  token: string;
  client: ClientKind;
  client_version: string;
  /** 端侧画布档位;主机按档截断推送内容(08 §6.4) */
  display_profile: DisplayProfile;
}

/** device.hello.result (H→D);status != ok 时主机发送结果后关闭连接 */
export type HelloStatus = "ok" | "pair_required" | "auth_failed" | "revoked";

export interface DeviceHelloResultPayload {
  status: HelloStatus;
  /** 服务器时间,用于端侧校时 */
  server_time: number;
  /**
   * ok 时必填,回显端侧身份:A0 原型期(dev_mode=auto_approve)由端侧生成稳定 id、
   * 主机登记后回显;端侧必须持久化并用于音频上传 X-Device-Id,禁止空值上传
   */
  device_id?: string;
}

/** device.pair.request (D→H) — 请求配对 */
export interface DevicePairRequestPayload {
  /** 端侧生成并展示的 6 位数字配对码(5 分钟有效、一次性) */
  pair_code: string;
  device_name: string;
}

/** device.pair.result (H→D);device_id/token 仅 approved 时存在 */
export type PairStatus = "approved" | "rejected" | "expired";

export interface DevicePairResultPayload {
  status: PairStatus;
  device_id?: string;
  /** 端侧持久化 */
  token?: string;
}

/** device.revoke (H→D) — 吊销通知;设备收到后清除本地 token 与全部卡片,回未配对页 */
export interface DeviceRevokePayload {
  reason?: string;
}

/** heartbeat(双向)— payload 为空对象;30s 间隔,连续 2 次未达即断连 */
export type HeartbeatPayload = Record<string, never>;

/* ---------- 2.2 录音与上传 ---------- */

/** 录音模式:auto / field(现场记录)/ experience(经验沉淀) */
export type RecordMode = "auto" | "field" | "experience";

/** record.start (D→H);record_id 端侧生成,幂等键 */
export interface RecordStartPayload {
  record_id: string;
  mode: RecordMode;
  /** 按键时刻(Unix 毫秒) */
  started_at: number;
}

/** record.stop (D→H);duration_ms < 2000 视为误触,端侧不上传音频 */
export interface RecordStopPayload {
  record_id: string;
  duration_ms: number;
}

/** 音频格式(HTTP 上传头 X-Audio-Format) */
export type AudioFormat = "webm-opus" | "opus" | "wav";

/** POST /audio/{record_id} 响应;duplicate 为幂等重传的首次受理回执 */
export interface AudioUploadResult {
  status: "received" | "duplicate";
  record_id: string;
}

/* ---------- 2.3 意图与执行 ---------- */

/** intent.result (H→D) — 处理终态或中间态 */
export type IntentStatus = "success" | "failed" | "pending_confirm" | "clarify" | "low_confidence";

/** 歧义候选(status=clarify 时必填,≤5 条) */
export interface ClarifyCandidate {
  candidate_id: string;
  label: string;
}

export interface IntentResultPayload {
  record_id: string;
  status: IntentStatus;
  /** 端侧大标题,如 "已完成:周报撰写" */
  title: string;
  body?: string;
  candidates?: ClarifyCandidate[];
  /** failed 时必填,见 ErrorCode */
  error_code?: ErrorCode;
}

/** clarify.select (D→H) — 歧义候选选择;选择后流程回到执行,结果仍走 intent.result */
export interface ClarifySelectPayload {
  record_id: string;
  candidate_id: string;
}

/** confirm.request (H→D) — L2 风险确认(宪法第 5 条) */
export interface ConfirmRequestPayload {
  confirm_id: string;
  record_id: string;
  /** 后果文案,如 "将向客户A发送《XX方案 v3》" */
  title: string;
  /** 参数明细 */
  body?: string;
  /** 默认 15;超时主机自动按 cancel 处理 */
  timeout_s: number;
}

/** confirm.response (D→H) */
export type ConfirmDecision = "confirm" | "cancel";

export interface ConfirmResponsePayload {
  confirm_id: string;
  decision: ConfirmDecision;
}

/* ---------- 2.4 卡片与简报 ---------- */

/** 卡片仅两类(宪法第 4 条) */
export type CardKind = "task" | "timer";

/** 提醒卡片(reminder.push 的 card;state.sync 复用同一结构) */
export interface Card {
  card_id: string;
  kind: CardKind;
  title: string;
  body?: string | null;
  remind_at?: string | null;
}

/** 简报条目;kind ∈ event/task/conflict,顺序即展示顺序,≤5 条 */
export type BriefItemKind = "event" | "task" | "conflict";

export interface BriefItem {
  kind: BriefItemKind;
  title: string;
  time?: string | null;
  /** 来源 task/event id,可追溯(FR-06) */
  source_id: string;
}

/** brief.push (H→D) */
export interface BriefPushPayload {
  briefing_id: string;
  /** YYYY-MM-DD */
  date: string;
  items: BriefItem[];
}

/** reminder.push (H→D) */
export interface ReminderPushPayload {
  card: Card;
}

/** reminder.dismiss (H→D);语音完成任务 → ≤5s 撤下(08 §1.3) */
export type DismissReason = "completed" | "cancelled" | "expired";

export interface ReminderDismissPayload {
  card_id: string;
  reason: DismissReason;
}

/** card.ack (D→H) — 端侧确认已展示/已撤下 */
export type CardAckAction = "displayed" | "dismissed";

export interface CardAckPayload {
  card_id: string;
  action: CardAckAction;
}

/* ---------- 2.5 通用 ---------- */

/** ack(双向)— 以信封 id 关联被应答消息(ref_id) */
export interface AckError {
  code: string;
  message: string;
}

export interface AckPayload {
  ref_id: string;
  status: "ok" | "error";
  error?: AckError;
}

/* ---------- 2.6 状态同步 ---------- */

/** state.sync (H→D) — 认证成功后或端侧请求时全量下发;重连后恢复状态的唯一入口(08 §1.1) */
export interface StateSyncPayload {
  /** 全部 active 卡片,可为空数组 */
  cards: Card[];
  /** 当日简报,无则省略 */
  briefing?: BriefPushPayload;
  /** 未决确认请求,无则省略 */
  pending_confirm?: ConfirmRequestPayload;
}

/** state.sync.request (D→H) — payload 为空,端侧判断状态脏时主动请求 */
export type StateSyncRequestPayload = Record<string, never>;

/* ---------- 3. 错误码 ---------- */

/** 错误码(登记册 §3) */
export const ErrorCode = {
  /** token 无效 → 回未配对页 */
  AuthFailed: "AUTH_FAILED",
  /** 已吊销 → 清除数据回未配对页 */
  DeviceRevoked: "DEVICE_REVOKED",
  /** 转写失败 → 提示重说 */
  AsrFailed: "ASR_FAILED",
  /** 转写置信度低 → 提示到安静处或 PC 补录 */
  AsrLowConfidence: "ASR_LOW_CONFIDENCE",
  /** 未理解 → 提示换说法或到 PC 操作 */
  IntentUnknown: "INTENT_UNKNOWN",
  /** 任务歧义(伴随 clarify)→ 展示候选 */
  TaskAmbiguous: "TASK_AMBIGUOUS",
  /** 办公系统不可达 → 提示稍后在 PC 重试 */
  BackendUnreachable: "BACKEND_UNREACHABLE",
  /** 确认超时自动取消 → 展示"已取消" */
  ConfirmTimeout: "CONFIRM_TIMEOUT",
  /** 触发过频 → 稍后再试 */
  RateLimited: "RATE_LIMITED",
  /** 未分类错误 → 提示并留日志 */
  Internal: "INTERNAL",
} as const;

export type ErrorCode = (typeof ErrorCode)[keyof typeof ErrorCode];

/* ---------- 消息类型汇总(登记册 §1.4 方向约定) ---------- */

/** type → payload 映射 */
export interface MessagePayloadMap {
  "device.hello": DeviceHelloPayload;
  "device.hello.result": DeviceHelloResultPayload;
  "device.pair.request": DevicePairRequestPayload;
  "device.pair.result": DevicePairResultPayload;
  "device.revoke": DeviceRevokePayload;
  heartbeat: HeartbeatPayload;
  "record.start": RecordStartPayload;
  "record.stop": RecordStopPayload;
  "intent.result": IntentResultPayload;
  "clarify.select": ClarifySelectPayload;
  "confirm.request": ConfirmRequestPayload;
  "confirm.response": ConfirmResponsePayload;
  "brief.push": BriefPushPayload;
  "reminder.push": ReminderPushPayload;
  "reminder.dismiss": ReminderDismissPayload;
  "card.ack": CardAckPayload;
  ack: AckPayload;
  "state.sync": StateSyncPayload;
  "state.sync.request": StateSyncRequestPayload;
}

export type MessageType = keyof MessagePayloadMap;

/** 带具体 payload 类型的消息联合 */
export type Message = {
  [K in MessageType]: Envelope<K, MessagePayloadMap[K]>;
}[MessageType];

/** D→H 消息类型(heartbeat/ack 双向,两侧均含) */
export type DeviceToHostType =
  | "device.hello"
  | "device.pair.request"
  | "record.start"
  | "record.stop"
  | "clarify.select"
  | "confirm.response"
  | "card.ack"
  | "state.sync.request"
  | "heartbeat"
  | "ack";

/** H→D 消息类型(heartbeat/ack 双向,两侧均含) */
export type HostToDeviceType =
  | "device.hello.result"
  | "device.pair.result"
  | "device.revoke"
  | "intent.result"
  | "confirm.request"
  | "brief.push"
  | "reminder.push"
  | "reminder.dismiss"
  | "state.sync"
  | "heartbeat"
  | "ack";

export type DeviceToHostMessage = {
  [K in DeviceToHostType]: Envelope<K, MessagePayloadMap[K]>;
}[DeviceToHostType];

export type HostToDeviceMessage = {
  [K in HostToDeviceType]: Envelope<K, MessagePayloadMap[K]>;
}[HostToDeviceType];

/** 构造消息信封(id 用 uuid,ts 用当前毫秒) */
export function makeEnvelope<K extends MessageType>(
  type: K,
  payload: MessagePayloadMap[K],
): Envelope<K, MessagePayloadMap[K]> {
  return { type, version: PROTOCOL_VERSION, id: crypto.randomUUID(), ts: Date.now(), payload };
}
