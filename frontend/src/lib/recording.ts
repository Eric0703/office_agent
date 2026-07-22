/**
 * 单键切换录音控制器(FR-02;08 §1.1 单键语义,废弃按住说话)。
 * 点击开始 → 静态"录音中" + 黑条 + "点击结束"(无每秒计时);<2s 静默丢弃;
 * 满 3 分钟震动+界面提醒;满 5 分钟自动停止并按正常流程上传。
 * 网络失败的音频入 IndexedDB 离线队列,重连后自动补传(OfflineCached);
 * 录音唯一合法触发仍是人点击(宪法第 2 条)。
 */
import { reactive } from "vue";

import { DeviceEvent } from "../state/machine";
import { connectionStore } from "../stores/connection";
import { vibrate } from "./haptics";
import { enqueue, listAll, remove, type PendingAudio } from "./pending-audio";
import { BadgeRecorder } from "./recorder";
import { wsClient } from "./ws-client";

const MIN_DURATION_MS = 2_000; // 不足 2s 误触,静默丢弃(FR-02)
const REMIND_MS = 180_000; // 满 3 分钟提醒
const MAX_DURATION_MS = 300_000; // 满 5 分钟自动停止

/** 录音进行中的界面状态(时长、3 分钟提醒) */
export const recordingUi = reactive({
  elapsedSec: 0,
  reminded: false,
});

const recorder = new BadgeRecorder();
let recordId = "";
let startedAt = 0;
let tickTimer: number | undefined;
let remindTimer: number | undefined;
let maxTimer: number | undefined;

/** 轻提示:3s 后自动清除 */
export function notify(text: string): void {
  connectionStore.notice = text;
  setTimeout(() => {
    if (connectionStore.notice === text) {
      connectionStore.notice = "";
    }
  }, 3_000);
}

/** 录音键单键切换:idle 点击开始,recording 再点结束;其余状态不响应 */
export async function toggleRecording(): Promise<void> {
  if (connectionStore.state === "idle") {
    await start();
  } else if (connectionStore.state === "recording") {
    await stop(false);
  }
}

async function start(): Promise<void> {
  recordId = crypto.randomUUID();
  startedAt = Date.now();
  try {
    await recorder.start();
  } catch {
    notify("无法使用麦克风"); // 权限被拒:不进入录音态
    return;
  }
  wsClient.send("record.start", { record_id: recordId, mode: "auto", started_at: startedAt });
  connectionStore.dispatch(DeviceEvent.RecordStartClick);
  vibrate(20);
  recordingUi.elapsedSec = 0;
  recordingUi.reminded = false;
  tickTimer = window.setInterval(() => {
    recordingUi.elapsedSec = Math.floor((Date.now() - startedAt) / 1000);
  }, 1_000);
  remindTimer = window.setTimeout(() => {
    recordingUi.reminded = true;
    vibrate([80, 50, 80]);
  }, REMIND_MS);
  maxTimer = window.setTimeout(() => {
    void stop(true);
  }, MAX_DURATION_MS);
}

async function stop(auto: boolean): Promise<void> {
  if (connectionStore.state !== "recording") {
    return; // 已停止(如自动停止与点击竞态)
  }
  window.clearInterval(tickTimer);
  window.clearTimeout(remindTimer);
  window.clearTimeout(maxTimer);
  const durationMs = Date.now() - startedAt;
  const blob = await recorder.stop();
  if (durationMs < MIN_DURATION_MS) {
    connectionStore.dispatch(DeviceEvent.RecordCancel); // 误触,静默丢弃
    return;
  }
  wsClient.send("record.stop", { record_id: recordId, duration_ms: durationMs });
  connectionStore.dispatch(auto ? DeviceEvent.RecordAutoStop : DeviceEvent.RecordStopClick);
  await upload(recordId, blob, durationMs);
}

/**
 * HTTP 音频通道上传(登记册 §2.2)。
 * 禁止空 X-Device-Id 上传;恢复凭据(A1-2):一切上传先入队,
 * 终态 intent.result(success/failed/low_confidence)到达才出队——
 * 200 受理后页面关闭/断连,重连经 duplicate 补推恢复;4xx 永久拒绝即时移除。
 */
async function upload(id: string, blob: Blob, durationMs: number): Promise<void> {
  if (!connectionStore.deviceId) {
    notify("未连接,等待重连");
    connectionStore.dispatch(DeviceEvent.UploadRejected);
    return;
  }
  // 先入队(恢复凭据):受理/断线/页面关闭后结果仍可恢复,终态到达才出队
  const item: PendingAudio = {
    record_id: id,
    blob,
    duration_ms: durationMs,
    queued_at: Date.now(),
    fmt: "webm-opus",
  };
  await enqueue(item);
  try {
    const resp = await fetch(`/audio/${id}`, {
      method: "POST",
      headers: {
        "X-Device-Id": connectionStore.deviceId,
        "X-Token": connectionStore.token,
        "X-Audio-Format": "webm-opus",
        "X-Duration-Ms": String(durationMs),
      },
      body: blob,
    });
    if (resp.ok) {
      // 已受理:凭据保留,终态经 WS 到达后由 ws-client 出队
      connectionStore.dispatch(DeviceEvent.UploadDone);
      return;
    }
    if (resp.status < 500) {
      // 4xx 永久拒绝(认证/超限等,无处理无终态):移除凭据,不重试
      await remove(id);
      notify(`上传被拒绝(HTTP ${resp.status})`);
      connectionStore.dispatch(DeviceEvent.UploadRejected);
      return;
    }
    // 5xx 暂时性故障:保留凭据,自动补传重试(与网络失败同路径)
  } catch {
    // 网络失败:保留凭据,自动补传重试
  }
  notify("已离线缓存,恢复后自动补传");
  connectionStore.dispatch(DeviceEvent.ConnectionLost);
  wsClient.forceReconnect();
}

const RETRY_DELAYS_MS = [2_000, 5_000, 10_000]; // 自动退避重试(5xx/网络错误)

type UploadOutcome = "ok" | "retry" | "reject";

/** 单次补传尝试:ok=受理(含 duplicate,结果由服务端补推);retry=5xx/网络;reject=4xx 永久 */
async function uploadOnce(item: PendingAudio): Promise<UploadOutcome> {
  try {
    const resp = await fetch(`/audio/${item.record_id}`, {
      method: "POST",
      headers: {
        "X-Device-Id": connectionStore.deviceId,
        "X-Token": connectionStore.token,
        "X-Audio-Format": item.fmt ?? "webm-opus",
        "X-Duration-Ms": String(item.duration_ms),
      },
      body: item.blob,
    });
    if (resp.ok) {
      return "ok";
    }
    return resp.status >= 500 ? "retry" : "reject";
  } catch {
    return "retry";
  }
}

/**
 * 重连后自动补传(08 §1.1 OfflineCached → Uploading):
 * 队列逐条重发(record_id 幂等,服务端去重);5xx/网络错误自动退避重试,
 * 4xx 永久拒绝丢弃并提示(不静默吞队列);有实际补传才推进 UploadDone。
 */
export async function flushPendingAudio(): Promise<void> {
  let flushed = 0;
  for (const item of await listAll()) {
    let outcome: UploadOutcome = "retry";
    for (const delay of [0, ...RETRY_DELAYS_MS]) {
      if (delay > 0) {
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
      outcome = await uploadOnce(item);
      if (outcome !== "retry") {
        break;
      }
    }
    if (outcome === "ok") {
      // 受理(含 duplicate)不删除:终态 intent.result 到达才出队(A1-2);
      // 服务端补推/合成或处理完成推送均经 WS 到达,出队在 ws-client 统一处理
      flushed += 1;
      continue;
    }
    if (outcome === "reject") {
      await remove(item.record_id);
      notify("有一条音频上传被拒绝,已丢弃");
      continue;
    }
    return; // 退避用尽仍失败:保留队列,下次重连再试
  }
  if (flushed > 0) {
    connectionStore.dispatch(DeviceEvent.UploadDone);
  }
}

// 重连成功(hello ok + state.sync)即自动补传;模块加载时注册一次
wsClient.onRestored(() => {
  void flushPendingAudio();
});
