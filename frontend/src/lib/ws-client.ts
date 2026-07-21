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
import { displayProfile } from "../stores/ui";
import { vibrate } from "./haptics";

const WS_URL = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
const MAX_RETRY_DELAY_MS = 15_000;
const SHOW_RESULT_MS = 3_000; // 08 §6.1:成功结果 3s 自动返回;失败/未听清停留至点击返回

export class WsClient {
  private ws: WebSocket | null = null;
  private retries = 0;
  private closedByUser = false;
  private resultTimer: number | undefined;

  /** 建立连接;onopen 即发 device.hello(原型期 dev_mode 直通配对) */
  connect(): void {
    this.closedByUser = false;
    connectionStore.dispatch(DeviceEvent.PairCodeSubmit); // unpaired → pairing
    this.ws = new WebSocket(WS_URL);
    this.ws.onopen = () => {
      this.send("device.hello", {
        device_id: connectionStore.deviceId,
        token: connectionStore.token,
        client: "vbadge-web",
        client_version: "0.1.0",
        display_profile: displayProfile(), // 按当前 eink 档声明(登记册 §2.1)
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
      if (!this.closedByUser) {
        this.scheduleReconnect();
      }
    };
    this.ws.onerror = () => this.ws?.close();
  }

  /** 发送一条 D→H 消息(自动包信封;未连接时丢弃,原型期不做离线队列) */
  send<K extends MessageType>(type: K, payload: MessagePayloadMap[K]): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(makeEnvelope(type, payload)));
    }
  }

  disconnect(): void {
    this.closedByUser = true;
    this.ws?.close();
  }

  /** 指数退避重连(08 §1.1 Reconnecting) */
  private scheduleReconnect(): void {
    connectionStore.dispatch(DeviceEvent.ConnectionLost);
    connectionStore.dispatch(DeviceEvent.ReconnectRetry);
    const delay = Math.min(1000 * 2 ** this.retries, MAX_RETRY_DELAY_MS);
    this.retries += 1;
    setTimeout(() => this.connect(), delay);
  }

  /** H→D 消息分发:更新 stores 并驱动状态机 */
  private handle(msg: HostToDeviceMessage): void {
    switch (msg.type) {
      case "device.hello.result":
        if (msg.payload.status === "ok") {
          this.retries = 0;
          connectionStore.connected = true;
          // 以主机回显的 device_id 为准并持久化(登记册 §2.1)
          if (msg.payload.device_id) {
            connectionStore.setDeviceId(msg.payload.device_id);
          }
          // 首连:pairing → idle;重连:reconnecting → idle(不适用的事件被状态机忽略)
          connectionStore.dispatch(DeviceEvent.PairApproved);
          connectionStore.dispatch(DeviceEvent.ConnectionRestored);
        }
        // pair_required/auth_failed/revoked:停留未配对页(正式配对流程后续任务卡)
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
        connectionStore.dispatch(DeviceEvent.IntentResult);
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
      case "ack":
        break;
    }
  }
}

export const wsClient = new WsClient();
