/**
 * 端侧运行时状态机(08 §1.1;与硬件固件共用语义)。
 * 要点:任何状态下连接中断都不丢已录音频(OfflineCached);state.sync 是重连后恢复的唯一入口。
 */

export const DeviceState = {
  Unpaired: "unpaired",
  Pairing: "pairing",
  Idle: "idle",
  Recording: "recording",
  Uploading: "uploading",
  Processing: "processing",
  ConfirmWait: "confirm_wait",
  Showing: "showing",
  Reconnecting: "reconnecting",
  OfflineCached: "offline_cached",
} as const;

export type DeviceState = (typeof DeviceState)[keyof typeof DeviceState];

/** 迁移事件(语义对应 08 §1.1 图上的触发条件与协议消息;单键切换语义) */
export const DeviceEvent = {
  /** 输入配对码 */
  PairCodeSubmit: "pair_code.submit",
  /** 主机确认 + state.sync */
  PairApproved: "pair.approved",
  /** 超时/拒绝 */
  PairRejected: "pair.rejected",
  /** 点击录音键开始录音(record.start) */
  RecordStartClick: "record.start_click",
  /** 再次点击结束(record.stop + audio.upload) */
  RecordStopClick: "record.stop_click",
  /** 满 5 分钟自动停止(防遗忘兜底) */
  RecordAutoStop: "record.auto_stop",
  /** 不足 2s 误触,静默丢弃 */
  RecordCancel: "record.cancel",
  /** 上传完成(红条熄灭) */
  UploadDone: "upload.done",
  /** 上传被主机拒绝(HTTP 非 200,非网络失败):回 Idle;08 未列此分支,端侧本地补充 */
  UploadRejected: "upload.rejected",
  /** 无连接,本地缓存 / 连接中断 */
  ConnectionLost: "connection.lost",
  /** 恢复连接(自动补传 / 重连成功 + state.sync) */
  ConnectionRestored: "connection.restored",
  /** 退避重试 */
  ReconnectRetry: "reconnect.retry",
  /** intent.result(成功/失败) */
  IntentResult: "intent.result",
  /** confirm.request(L2 风险) */
  ConfirmRequest: "confirm.request",
  /** 确认 / 取消 / 15s 超时 */
  ConfirmResolved: "confirm.resolved",
  /** 展示 3s 回身份页 */
  ShowTimeout: "show.timeout",
  /** reminder.push / brief.push(震动提示) */
  PushReceived: "push.received",
  /** 被吊销 */
  DeviceRevoked: "device.revoked",
} as const;

export type DeviceEvent = (typeof DeviceEvent)[keyof typeof DeviceEvent];

/** 迁移表(08 §1.1 图的逐边转写) */
const TRANSITIONS: Readonly<Record<DeviceState, Readonly<Partial<Record<DeviceEvent, DeviceState>>>>> =
  {
    [DeviceState.Unpaired]: {
      [DeviceEvent.PairCodeSubmit]: DeviceState.Pairing,
    },
    [DeviceState.Pairing]: {
      [DeviceEvent.PairApproved]: DeviceState.Idle,
      [DeviceEvent.PairRejected]: DeviceState.Unpaired,
    },
    [DeviceState.Idle]: {
      [DeviceEvent.RecordStartClick]: DeviceState.Recording,
      [DeviceEvent.PushReceived]: DeviceState.Idle,
      [DeviceEvent.ConnectionLost]: DeviceState.Reconnecting,
      [DeviceEvent.DeviceRevoked]: DeviceState.Unpaired,
    },
    [DeviceState.Recording]: {
      [DeviceEvent.RecordStopClick]: DeviceState.Uploading,
      [DeviceEvent.RecordAutoStop]: DeviceState.Uploading,
      [DeviceEvent.RecordCancel]: DeviceState.Idle,
    },
    [DeviceState.Uploading]: {
      [DeviceEvent.UploadDone]: DeviceState.Processing,
      [DeviceEvent.ConnectionLost]: DeviceState.OfflineCached,
      [DeviceEvent.UploadRejected]: DeviceState.Idle,
    },
    [DeviceState.OfflineCached]: {
      [DeviceEvent.ConnectionRestored]: DeviceState.Uploading,
    },
    [DeviceState.Processing]: {
      [DeviceEvent.IntentResult]: DeviceState.Showing,
      [DeviceEvent.ConfirmRequest]: DeviceState.ConfirmWait,
    },
    [DeviceState.ConfirmWait]: {
      [DeviceEvent.ConfirmResolved]: DeviceState.Showing,
    },
    [DeviceState.Showing]: {
      [DeviceEvent.ShowTimeout]: DeviceState.Idle,
    },
    [DeviceState.Reconnecting]: {
      [DeviceEvent.ConnectionRestored]: DeviceState.Idle,
      [DeviceEvent.ReconnectRetry]: DeviceState.Reconnecting,
    },
  };

export const INITIAL_STATE: DeviceState = DeviceState.Unpaired;

/** 迁移函数:未定义的迁移视为忽略,停留原态(骨架语义,后续可按需改为抛错) */
export function transition(current: DeviceState, event: DeviceEvent): DeviceState {
  return TRANSITIONS[current][event] ?? current;
}
