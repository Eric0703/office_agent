/**
 * WS 控制通道客户端(登记册 §1.1):连接/hello 认证/退避重连/消息分发到 stores。
 * 认证成功或重连后靠 state.sync 恢复卡片与状态(08 §1.1)。
 */
import {
  makeEnvelope,
  type HostToDeviceMessage,
  type MessagePayloadMap,
  type MessageType,
} from "../protocol/messages";
import { DeviceEvent } from "../state/machine";
import { cardsStore } from "../stores/cards";
import { connectionStore } from "../stores/connection";
import { displayProfile, uiStore } from "../stores/ui";
import { vibrate } from "./haptics";
import { remove as removePendingAudio } from "./pending-audio";

const WS_URL = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
const MAX_RETRY_DELAY_MS = 15_000;
const SHOW_RESULT_MS = 3_000; // 08 §6.1:成功结果 3s 自动返回;失败/未听清停留至点击返回
const HEARTBEAT_INTERVAL_MS = 30_000; // 登记册 §2.1:30s 间隔,连续 2 次未达即断连
/** 终态状态集:仅这些 intent.result 允许出队恢复凭据;clarify/pending_confirm 为中间态 */
const TERMINAL_STATUSES = new Set(["success", "failed", "low_confidence"]);

export class WsClient {
  private ws: WebSocket | null = null;
  private retries = 0;
  private closedByUser = false;
  private resultTimer: number | undefined;
  /** 重连定时器去重:任意时刻最多一个待触发重连(竞态修复) */
  private reconnectTimer: number | undefined;
  /** 配对模式:连接首条消息发 device.pair.request 而非 hello(登记册 §2.1) */
  private pairing = false;
  /** 重连成功(hello ok + state.sync)钩子:离线音频补传等 */
  private restoredHooks: Array<() => void> = [];

  /** 注册重连成功钩子(模块加载期调用一次) */
  onRestored(cb: () => void): void {
    this.restoredHooks.push(cb);
  }

  /** 建立连接;onopen 按模式发 device.hello 或 device.pair.request */
  connect(): void {
    this.closedByUser = false;
    // 取消遗留重连定时器:每次连接切换只建立一个新连接
    window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = undefined;
    connectionStore.dispatch(DeviceEvent.PairCodeSubmit); // unpaired → pairing
    const ws = new WebSocket(WS_URL);
    this.ws = ws;
    ws.onopen = () => {
      if (this.pairing && connectionStore.pairCode) {
        this.send("device.pair.request", {
          pair_code: connectionStore.pairCode,
          device_name: "虚拟工牌(PWA)",
        });
        return;
      }
      this.send("device.hello", {
        device_id: connectionStore.deviceId,
        token: connectionStore.token,
        client: "vbadge-web",
        client_version: "0.1.0",
        display_profile: displayProfile(), // 按当前 eink 档声明(登记册 §2.1)
        capabilities: {
          // 方案 A 统一能力声明:webm-opus 单声道音频 / 300×400 电子纸 / 三键 / LED+震动 / Wi-Fi
          audio: { formats: ["webm-opus"], channels: 1 },
          screen: { type: "eink", width: 300, height: 400, profile: displayProfile() },
          keys: ["action", "page_up", "page_down"],
          led: true,
          haptics: true,
          network: ["wifi"],
        },
      });
    };
    this.ws.onmessage = (ev: MessageEvent<string>) => {
      try {
        this.handle(JSON.parse(ev.data) as HostToDeviceMessage);
      } catch {
        // 忽略无法解析的帧;接收方必须容错未知字段(登记册 §1.3)
      }
    };
    this.ws.onclose = () => {
      connectionStore.connected = false;
      this.stopHeartbeat();
      if (this.ws !== ws) {
        return; // 旧连接迟到的 onclose:忽略,不得触发重连(竞态修复)
      }
      if (!this.closedByUser) {
        this.scheduleReconnect();
      }
    };
    this.ws.onerror = () => ws.close();
  }

  /** 发送一条 D→H 消息(自动包信封;未连接时丢弃,原型期不做离线队列);返回信封 id */
  send<K extends MessageType>(type: K, payload: MessagePayloadMap[K]): string {
    if (this.ws?.readyState !== WebSocket.OPEN) {
      return "";
    }
    const envelope = makeEnvelope(type, payload);
    this.ws.send(JSON.stringify(envelope));
    return envelope.id;
  }

  disconnect(): void {
    this.closedByUser = true;
    this.stopHeartbeat();
    window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = undefined;
    this.closeSilently();
  }

  /** 网络层失败判定连接已死(如音频上传失败):主动断开,由 onclose 退避重连(08 §1.1)。
   *  用于假死场景——部分环境(如模拟器/代理)网络断开时 WS 不会自动关闭。 */
  forceReconnect(): void {
    this.ws?.close();
  }

  /** 静默关闭当前连接:先摘除全部回调,杜绝旧 onclose 迟发再次触发重连(竞态修复) */
  private closeSilently(): void {
    const ws = this.ws;
    if (!ws) {
      return;
    }
    ws.onopen = null;
    ws.onmessage = null;
    ws.onerror = null;
    ws.onclose = null;
    this.ws = null;
    try {
      ws.close();
    } catch {
      // 已关闭
    }
  }

  /* ---------- 心跳(登记册 §2.1):30s 间隔,同 id 校验,连续 2 次未达主动断连 ---------- */

  private hbTimer: number | undefined;
  private hbPendingId = "";
  private hbMissed = 0;

  /** hello 认证成功后启动心跳 */
  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.hbTimer = window.setInterval(() => this.beat(), HEARTBEAT_INTERVAL_MS);
  }

  private stopHeartbeat(): void {
    window.clearInterval(this.hbTimer);
    this.hbTimer = undefined;
    this.hbPendingId = "";
    this.hbMissed = 0;
  }

  /** 上一次未收到同 id 回应记一次未达;连续 2 次主动断连,由 onclose 退避重连 */
  private beat(): void {
    if (this.hbPendingId) {
      this.hbMissed += 1;
      if (this.hbMissed >= 2) {
        this.stopHeartbeat();
        this.ws?.close();
        return;
      }
    }
    this.hbPendingId = this.send("heartbeat", {});
  }

  /** 指数退避重连(08 §1.1 Reconnecting);任意时刻最多一个待触发定时器 */
  private scheduleReconnect(): void {
    connectionStore.dispatch(DeviceEvent.ConnectionLost);
    connectionStore.dispatch(DeviceEvent.ReconnectRetry);
    const delay = Math.min(1000 * 2 ** this.retries, MAX_RETRY_DELAY_MS);
    this.retries += 1;
    window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = window.setTimeout(() => this.connect(), delay);
  }

  /** H→D 消息分发:更新 stores 并驱动状态机 */
  private handle(msg: HostToDeviceMessage): void {
    switch (msg.type) {
      case "device.hello.result":
        if (msg.payload.status === "ok") {
          this.retries = 0;
          this.pairing = false;
          connectionStore.pairCode = "";
          connectionStore.connected = true;
          // 以主机回显的 device_id 为准并持久化(登记册 §2.1)
          if (msg.payload.device_id) {
            connectionStore.setDeviceId(msg.payload.device_id);
          }
          // 首连:pairing → idle;重连:reconnecting → idle(不适用的事件被状态机忽略)
          connectionStore.dispatch(DeviceEvent.PairApproved);
          connectionStore.dispatch(DeviceEvent.ConnectionRestored);
          // 认证成功(重连后服务端随即发 state.sync):启动 30s 心跳
          this.startHeartbeat();
          // 重连成功钩子(离线音频自动补传等,08 §1.1)
          for (const cb of this.restoredHooks) {
            cb();
          }
        } else if (msg.payload.status === "pair_required") {
          // 未配对:生成 6 位配对码,重连后改发 device.pair.request(登记册 §2.1)
          this.pairing = true;
          if (!connectionStore.pairCode) {
            connectionStore.pairCode = String(Math.floor(100000 + Math.random() * 900000));
          }
        } else {
          // revoked / auth_failed:清除身份回未配对页,重新走配对
          this.pairing = false;
          connectionStore.reset();
        }
        break;
      case "device.pair.result":
        if (msg.payload.status === "approved" && msg.payload.device_id && msg.payload.token) {
          // 批准:持久化 token/device_id,静默关闭旧连接后重连走 hello 认证进入 idle
          connectionStore.token = msg.payload.token;
          localStorage.setItem("vbadge_token", msg.payload.token);
          connectionStore.setDeviceId(msg.payload.device_id);
          connectionStore.pairCode = "";
          this.pairing = false;
          this.closeSilently();
          this.connect();
        } else {
          // rejected / expired:立即生成新码并重发 pair.request,不停留在无配对码的等待态;
          // 静默关闭杜绝旧 onclose 迟发造成重复连接/重复 pair.request(竞态修复)
          connectionStore.pairCode = String(Math.floor(100000 + Math.random() * 900000));
          connectionStore.notice =
            msg.payload.status === "expired"
              ? "配对码已过期,已生成新配对码"
              : "配对被拒绝,已生成新配对码";
          this.pairing = true;
          this.closeSilently();
          this.connect();
        }
        break;
      case "state.sync":
        cardsStore.replaceAll(msg.payload.cards);
        if (msg.payload.briefing) {
          cardsStore.setBriefing(msg.payload.briefing);
        }
        if (msg.payload.pending_confirm) {
          connectionStore.pendingConfirm = msg.payload.pending_confirm;
          connectionStore.dispatch(DeviceEvent.ConfirmRequest);
        }
        break;
      case "intent.result":
        connectionStore.lastResult = msg.payload;
        if (msg.payload.status === "clarify") {
          uiStore.clarifyIndex = 0; // 新 clarify 到达:高亮归零(上翻/下翻移动起点)
        }
        connectionStore.dispatch(DeviceEvent.IntentResult);
        // 仅终态(success/failed/low_confidence)出队恢复凭据;clarify/pending_confirm
        // 是中间态,凭据保留——放弃/断线后经 duplicate 补推可回到同一中间态(A1-2)
        if (TERMINAL_STATUSES.has(msg.payload.status)) {
          void removePendingAudio(msg.payload.record_id);
        }
        window.clearTimeout(this.resultTimer);
        if (msg.payload.status === "success") {
          // 成功:展示 3s 自动返回原页面;clarify 等端侧选择;
          // 失败/未听清:停留,由用户点击返回,不让提示一闪而过(08 §6.1)
          this.resultTimer = window.setTimeout(() => {
            connectionStore.lastResult = null;
            connectionStore.dispatch(DeviceEvent.ShowTimeout);
          }, SHOW_RESULT_MS);
        }
        break;
      case "reminder.push":
        cardsStore.upsertCard(msg.payload.card);
        vibrate(30);
        break;
      case "reminder.dismiss":
        cardsStore.dismissCard(msg.payload.card_id, msg.payload.reason);
        break;
      case "confirm.request":
        connectionStore.pendingConfirm = msg.payload;
        connectionStore.dispatch(DeviceEvent.ConfirmRequest);
        vibrate([50, 50, 50]);
        break;
      case "device.revoke":
        connectionStore.reset(); // 清除 token 与全部卡片,回未配对页(登记册 §2.1)
        break;
      case "heartbeat":
        // pong 复用同一信封 id(登记册 §2.1);同 id 匹配才清零未达计数
        if (msg.id === this.hbPendingId) {
          this.hbPendingId = "";
          this.hbMissed = 0;
        }
        break;
      case "ack":
        break;
    }
  }
}

export const wsClient = new WsClient();
