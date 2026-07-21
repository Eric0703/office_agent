/**
 * 连接 store:WS 状态、配对身份(localStorage 持久化)、待展示结果与待决确认。
 * 用 vue reactive,不引入 Pinia(08 §2;规约 §7)。
 */
import { reactive } from "vue";

import type { ConfirmRequestPayload, IntentResultPayload } from "../protocol/messages";
import { DeviceEvent, DeviceState, INITIAL_STATE, transition } from "../state/machine";

const KEY_DEVICE_ID = "vbadge_device_id";
const KEY_TOKEN = "vbadge_token";

/** 首次启动生成稳定 UUID 并持久化(登记册 §2.1:A0 原型期端侧生成,主机登记回显) */
function loadDeviceId(): string {
  let id = localStorage.getItem(KEY_DEVICE_ID);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(KEY_DEVICE_ID, id);
  }
  return id;
}

export const connectionStore = reactive({
  /** 端侧状态机当前状态(08 §1.1) */
  state: INITIAL_STATE as DeviceState,
  /** WS 是否已连接并通过 hello 认证 */
  connected: false,
  /** 端侧稳定身份,上传音频的 X-Device-Id 来源;以 hello.result 回显值为准 */
  deviceId: loadDeviceId(),
  /** 配对 token(正式配对后持久化;原型期可为空) */
  token: localStorage.getItem(KEY_TOKEN) ?? "",
  /** 最近一条 intent.result(showing 态展示;终态 3s 后清除) */
  lastResult: null as IntentResultPayload | null,
  /** 未决 L2 确认请求(confirm_wait 态展示) */
  pendingConfirm: null as ConfirmRequestPayload | null,
  /** 轻提示(如"未连接,等待重连"),数秒后自动清除 */
  notice: "",
  /** 向状态机派发事件 */
  dispatch(event: DeviceEvent): void {
    this.state = transition(this.state, event);
  },
  /** 保存 hello.result 回显的 device_id(覆盖本地值并持久化) */
  setDeviceId(id: string): void {
    if (id && id !== this.deviceId) {
      this.deviceId = id;
      localStorage.setItem(KEY_DEVICE_ID, id);
    }
  },
  /** 被吊销:清除 token、身份与卡片相关状态,回未配对页(登记册 §2.1) */
  reset(): void {
    localStorage.removeItem(KEY_DEVICE_ID);
    localStorage.removeItem(KEY_TOKEN);
    this.deviceId = "";
    this.token = "";
    this.connected = false;
    this.lastResult = null;
    this.pendingConfirm = null;
    this.state = DeviceState.Unpaired;
  },
});
