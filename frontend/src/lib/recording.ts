/**
 * 单键切换录音控制器(FR-02;08 §1.1 单键语义,废弃按住说话)。
 * 点击开始 → 红条 + mm:ss + "点击结束";<2s 静默丢弃;满 3 分钟震动+界面提醒;
 * 满 5 分钟自动停止并按正常流程上传。录音唯一合法触发仍是人点击(宪法第 2 条)。
 */
import { reactive } from "vue";

import { DeviceEvent } from "../state/machine";
import { connectionStore } from "../stores/connection";
import { vibrate } from "./haptics";
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
 * 禁止空 X-Device-Id 上传;200 → processing 等 intent.result;
 * 仅网络层失败进 OfflineCached,HTTP 拒绝回 Idle 并提示。
 */
async function upload(id: string, blob: Blob, durationMs: number): Promise<void> {
  if (!connectionStore.deviceId) {
    notify("未连接,等待重连");
    connectionStore.dispatch(DeviceEvent.UploadRejected);
    return;
  }
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
      connectionStore.dispatch(DeviceEvent.UploadDone);
    } else {
      notify(`上传失败(HTTP ${resp.status})`);
      connectionStore.dispatch(DeviceEvent.UploadRejected);
    }
  } catch {
    connectionStore.dispatch(DeviceEvent.ConnectionLost);
  }
}
