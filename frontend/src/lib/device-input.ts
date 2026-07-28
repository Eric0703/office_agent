/**
 * 设备输入抽象(虚拟工牌硬件模拟):三枚物理键的唯一入口,分三层——
 *   物理键(3):主操作键 action(实体为录音/麦克风标识)/ 上翻 page_up / 下翻 page_down;
 *   手势(2):短按 short / 长按 long(≥600ms,由调用方计时;仅 action 有长按语义);
 *   语义动作:pressKey 按 键×手势×界面状态 分派到动作函数(发 WS + dispatch)。
 * PWA 虚拟物理键(.hw-keys)、手机演示按钮都经 pressKey 分派,不区分输入来源;
 * 未来 ESP32 GPIO 按键映射到同一语义。
 * 主操作键按界面状态复用(Owner 三键模型,第四枚"确认·返回"键已删除):
 *   普通页面(身份/卡片/简报):短按开始录音,长按返回身份首页(cardPage=0);
 *   录音中:短按结束并上传,长按 no-op;
 *   clarify 候选页:短按选定当前高亮候选,长按取消并退出(发 clarify.select 取消 id);
 *   失败/未听清结果页:短按关闭结果,长按返回身份首页;
 *   L2 确认页(confirm_wait):短按取消,长按确认(与旧四键相反);
 *   手机档多任务预览:短按全部创建,长按取消预览。
 * 上翻/下翻:翻页/候选高亮双向循环,长按 no-op;双击只预留、不实现,单击零延迟响应。
 * 页模型(方案 A):身份首页(0) → 待办/提醒卡 → 简报,上翻/下翻双向循环。
 * 注意:recording.ts 不得 import 本模块(避免环:本模块依赖 toggleRecording)。
 */
import { computed, ref, watch } from "vue";

import type { ConfirmDecision } from "../protocol/messages";
import { DeviceEvent } from "../state/machine";
import { cardsStore } from "../stores/cards";
import { connectionStore } from "../stores/connection";
import { uiStore } from "../stores/ui";
import { toggleRecording } from "./recording";
import { wsClient } from "./ws-client";

/** 设备物理键:主操作键(录音)/ 上翻 / 下翻 */
export type DeviceKey = "action" | "page_up" | "page_down";

/** 按键手势:短按 / 长按(≥600ms);非来源区分,任何输入来源都可携带手势 */
export type KeyGesture = "short" | "long";

/* ---------- 动作函数(原 CardsView/ConfirmView 内联实现,行为不变) ---------- */

/** 页模型翻页:身份首页(0) → 卡片 → 简报,±1 双向循环;列表变化时 clamp 防越界 */
export function turnPage(delta: 1 | -1): void {
  // +1 = 身份首页(方案 A:已连接默认页,有无待办均在)
  const count = cardsStore.cards.length + (cardsStore.briefing ? 1 : 0) + 1;
  const index = Math.min(uiStore.cardPage, count - 1);
  uiStore.cardPage = (index + delta + count) % count;
}

/** clarify 候选选择:发 clarify.select,结果仍走 intent.result(登记册 §2.3) */
export function pick(candidateId: string): void {
  const result = connectionStore.lastResult;
  if (!result) {
    return;
  }
  wsClient.send("clarify.select", { record_id: result.record_id, candidate_id: candidateId });
  connectionStore.lastResult = null;
  uiStore.clarifyIndex = 0;
}

/**
 * clarify 长按取消并退出:任务类发 task:cancel;提醒类(候选含 remind: 前缀)发 remind:cancel,
 * 端侧按候选前缀选择取消 id。发送后清 lastResult 与 clarifyIndex(与 pick 一致);
 * 服务端回终态 intent.result"已取消"(终态出队恢复凭据;duplicate 恢复重放该终态,不回候选页)。
 */
export function cancelClarify(): void {
  const result = connectionStore.lastResult;
  if (result?.status !== "clarify") {
    return;
  }
  const isReminder = (result.candidates ?? []).some((c) =>
    c.candidate_id.startsWith("remind:"),
  );
  pick(isReminder ? "remind:cancel" : "task:cancel");
}

/** L2 确认应答(原 ConfirmView.respond;登记册 §2.3 confirm.request/response) */
export function respond(decision: ConfirmDecision): void {
  const pending = connectionStore.pendingConfirm;
  if (!pending) {
    return;
  }
  wsClient.send("confirm.response", { confirm_id: pending.confirm_id, decision });
  connectionStore.pendingConfirm = null;
  connectionStore.dispatch(DeviceEvent.ConfirmResolved);
}

/** 多任务预览(手机档)的可编辑标题;新 clarify 正文到达时按编号列表初始化 */
export const previewTasks = ref<string[]>([]);

/** 多任务预览判定:clarify 且候选含"全部创建"(task:confirm_all) */
export const isTaskPreview = computed(
  () =>
    connectionStore.lastResult?.status === "clarify" &&
    (connectionStore.lastResult.candidates ?? []).some(
      (c) => c.candidate_id === "task:confirm_all",
    ),
);

watch(
  () => connectionStore.lastResult,
  (result) => {
    if (result?.status === "clarify" && result.body) {
      // 正文为编号预览("1. xxx"),初始化可编辑标题
      previewTasks.value = result.body
        .split("\n")
        .map((line) => line.replace(/^\d+[.、]\s*/, "").trim())
        .filter(Boolean);
    } else {
      previewTasks.value = [];
    }
  },
);

/** 全部创建:带端侧编辑后标题回传(edited_labels,登记册修订6) */
export function confirmPreview(): void {
  const result = connectionStore.lastResult;
  if (!result) {
    return;
  }
  wsClient.send("clarify.select", {
    record_id: result.record_id,
    candidate_id: "task:confirm_all",
    edited_labels: previewTasks.value.map((t) => t.trim()).filter(Boolean),
  });
  connectionStore.lastResult = null;
  uiStore.clarifyIndex = 0;
}

export function cancelPreview(): void {
  pick("task:cancel");
}

/** 失败/未听清结果:短按主操作键(或手机点击)关闭;成功 3s 自动返回(08 §6.1) */
export function dismissResult(): void {
  const result = connectionStore.lastResult;
  if (!result || !["failed", "low_confidence"].includes(result.status)) {
    return; // 成功结果自动返回;clarify 必须选定或取消,不响应关闭
  }
  connectionStore.lastResult = null;
  connectionStore.dispatch(DeviceEvent.ShowTimeout);
}

/* ---------- 语义键分派 ---------- */

/** 上翻/下翻:clarify 展示中移动高亮(±1 双向循环,B 档可见批次随高亮),否则页模型翻页 */
function pressPageTurn(delta: 1 | -1): void {
  const result = connectionStore.lastResult;
  if (result?.status === "clarify") {
    const count = result.candidates?.length ?? 0;
    if (count > 0) {
      const index = Math.min(uiStore.clarifyIndex, count - 1);
      uiStore.clarifyIndex = (index + delta + count) % count;
    }
    return;
  }
  turnPage(delta);
}

/** 主操作键短按:按界面状态复用(见模块头状态表) */
function pressActionShort(): void {
  // L2 确认页:短按 = 取消(与旧四键相反)
  if (connectionStore.state === "confirm_wait" && connectionStore.pendingConfirm) {
    respond("cancel");
    return;
  }
  const result = connectionStore.lastResult;
  if (result) {
    if (result.status === "clarify") {
      if (isTaskPreview.value && uiStore.eink === "off") {
        confirmPreview(); // 手机档多任务预览:短按 = 全部创建(带 edited_labels)
        return;
      }
      // clarify 候选页:短按选定当前高亮候选
      const candidates = result.candidates ?? [];
      const current = candidates[Math.min(uiStore.clarifyIndex, candidates.length - 1)];
      if (current) {
        pick(current.candidate_id);
      }
      return;
    }
    dismissResult(); // 失败/未听清结果页:短按关闭(成功结果 3s 自动返回,此处 no-op)
    return;
  }
  void toggleRecording(); // 普通页面:开始录音;录音中:结束并上传
}

/** 主操作键长按(≥600ms):按界面状态复用;录音中 no-op */
function pressActionLong(): void {
  // L2 确认页:长按 = 确认执行(与旧四键相反)
  if (connectionStore.state === "confirm_wait" && connectionStore.pendingConfirm) {
    respond("confirm");
    return;
  }
  if (connectionStore.state === "recording") {
    return; // 录音中:长按 no-op
  }
  const result = connectionStore.lastResult;
  if (result) {
    if (result.status === "clarify") {
      if (isTaskPreview.value && uiStore.eink === "off") {
        cancelPreview(); // 手机档多任务预览:长按 = 取消预览(发 task:cancel)
        return;
      }
      cancelClarify(); // clarify 候选页:长按取消并退出(按候选前缀选 task:/remind: 取消 id)
      return;
    }
    if (["failed", "low_confidence"].includes(result.status)) {
      // 失败/未听清结果页:长按关闭覆盖层并返回身份首页
      uiStore.cardPage = 0;
      dismissResult();
      return;
    }
    // success 等其余覆盖层:落入回身份首页(覆盖层 3s 后自动消失)
  }
  uiStore.cardPage = 0; // 普通页面:长按返回身份首页(方案 A:页 0 = 身份)
}

/** 设备按键唯一语义入口:任何来源(虚拟物理键/演示按钮/未来 GPIO)都调它;
 *  长按仅 action 有语义,page_up/page_down 长按 no-op;双击不实现,单击零延迟 */
export function pressKey(key: DeviceKey, gesture: KeyGesture = "short"): void {
  if (key !== "action") {
    if (gesture === "short") {
      pressPageTurn(key === "page_up" ? -1 : 1);
    }
    return;
  }
  if (gesture === "long") {
    pressActionLong();
    return;
  }
  pressActionShort();
}
