<script setup lang="ts">
// 卡片页(08 §6.1 页面模型):
//   默认页 = 当前最高优先 active 卡(单卡,不与简报堆叠);简报页 = 翻页进入的独立页;
//   无卡时 A 档显示身份页、B 档显示空态;结果(intent.result)与 clarify 全屏覆盖;
//   手机模式 = 顶对齐待办应用布局(触控勾选完成、多任务可编辑预览),与电子纸档样式隔离。
// 优先级规则(自定,已注明):remind_at 最近者优先(催办语义),无 remind_at 排最后,同刻按创建序。
import { computed, ref, watch } from "vue";

import { toggleRecording } from "../lib/recording";
import { wsClient } from "../lib/ws-client";
import type { Card, ClarifyCandidate } from "../protocol/messages";
import { DeviceEvent } from "../state/machine";
import { cardsStore } from "../stores/cards";
import { connectionStore } from "../stores/connection";
import { uiStore } from "../stores/ui";

const isEink = computed(() => uiStore.eink !== "off");
const isA = computed(() => uiStore.eink === "a");

/* ---------- 页序列:默认页(单卡 ×N)+ 简报页,翻页键循环 ---------- */

type Page = { kind: "card"; card: Card } | { kind: "briefing" };

/** 最高优先排序:remind_at 最近优先,无则最后(字符串序即时间序) */
const sortedCards = computed<Card[]>(() =>
  [...cardsStore.cards].sort((a, b) =>
    (a.remind_at ?? "9999") < (b.remind_at ?? "9999") ? -1 : 1,
  ),
);

const pages = computed<Page[]>(() => [
  ...sortedCards.value.map((card) => ({ kind: "card", card }) as Page),
  ...(cardsStore.briefing ? [{ kind: "briefing" } as Page] : []),
]);

const pageCount = computed(() => Math.max(pages.value.length, 1));

const pageIndex = computed(() => Math.min(uiStore.cardPage, pageCount.value - 1));

const currentPage = computed<Page | undefined>(() => pages.value[pageIndex.value]);

/** 翻页键(原型期以按钮模拟):默认页 ↔ 简报页循环 */
function nextPage(): void {
  uiStore.cardPage = (pageIndex.value + 1) % pageCount.value;
}

/* ---------- 触控勾选:任务卡完成任务,定时提醒卡取消提醒(服务端广播撤卡同步) ---------- */

async function completeCard(card: Card): Promise<void> {
  const url =
    card.kind === "task" && card.ref_task_id
      ? `/desk/tasks/${card.ref_task_id}/complete`
      : `/desk/reminders/${card.card_id}/cancel`;
  try {
    await fetch(url, { method: "POST" });
  } catch {
    // 网络失败:卡片保留,状态以服务端为准,用户可重试
  }
}

/* ---------- clarify:全屏选择;B 档 ≤2 个候选/页 ---------- */

const clarifyPage = ref(0);

const clarifySlice = computed<ClarifyCandidate[]>(() => {
  const all = connectionStore.lastResult?.candidates ?? [];
  if (uiStore.eink !== "b") {
    return all;
  }
  const start = (clarifyPage.value * 2) % Math.max(all.length, 1);
  return all.slice(start, start + 2);
});

const clarifyHasMore = computed(
  () => uiStore.eink === "b" && (connectionStore.lastResult?.candidates?.length ?? 0) > 2,
);

function nextClarifyPage(): void {
  clarifyPage.value += 1;
}

/** clarify 候选选择:发 clarify.select,结果仍走 intent.result(登记册 §2.3) */
function pick(candidateId: string): void {
  const result = connectionStore.lastResult;
  if (!result) {
    return;
  }
  wsClient.send("clarify.select", { record_id: result.record_id, candidate_id: candidateId });
  connectionStore.lastResult = null;
  clarifyPage.value = 0;
}

/* ---------- 多任务可编辑预览(手机模式;电子纸档以正文编号列表 + 确认/取消键呈现) ---------- */

const previewTasks = ref<string[]>([]);

/** 多任务预览判定:clarify 且候选含"全部创建"(task:confirm_all) */
const isTaskPreview = computed(
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
function confirmPreview(): void {
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
  clarifyPage.value = 0;
}

function cancelPreview(): void {
  pick("task:cancel");
}

/* ---------- 结果页:失败/未听清停留,由用户点击返回;成功 3s 自动返回(08 §6.1) ---------- */

const isFailedResult = computed(
  () =>
    connectionStore.lastResult !== null &&
    ["failed", "low_confidence"].includes(connectionStore.lastResult.status),
);

function dismissResult(): void {
  if (!isFailedResult.value) {
    return; // 成功结果自动返回;clarify 必须点候选,不响应空白点击
  }
  connectionStore.lastResult = null;
  connectionStore.dispatch(DeviceEvent.ShowTimeout);
}
</script>

<template>
  <section class="view" :class="{ phone: !isEink }">
    <!-- 结果页 / clarify 候选页:全屏覆盖;成功 3s 自动返回,失败/未听清停留至点击返回(页序号保持) -->
    <div v-if="connectionStore.lastResult" class="result" @click="dismissResult">
      <h1>{{ connectionStore.lastResult.title }}</h1>
      <p v-if="connectionStore.lastResult.body && !isTaskPreview">
        {{ connectionStore.lastResult.body }}
      </p>
      <p v-if="isFailedResult" class="hint">点击返回</p>
      <!-- 多任务预览(手机模式):可编辑标题,确认后批量创建;电子纸档走通用候选(正文编号列表+按键) -->
      <template v-if="isTaskPreview && !isEink">
        <input
          v-for="(_, i) in previewTasks"
          :key="i"
          v-model="previewTasks[i]"
          class="preview-input"
          :aria-label="`任务 ${i + 1}`"
        />
        <div class="preview-actions">
          <button class="candidate" @click="confirmPreview">全部创建</button>
          <button class="candidate" @click="cancelPreview">取消</button>
        </div>
      </template>
      <template v-else-if="connectionStore.lastResult.status === 'clarify'">
        <button
          v-for="c in clarifySlice"
          :key="c.candidate_id"
          class="candidate"
          @click="pick(c.candidate_id)"
        >
          {{ c.label }}
        </button>
        <button v-if="clarifyHasMore" class="candidate more" @click="nextClarifyPage">
          下一批
        </button>
      </template>
    </div>

    <!-- 电子纸档(A/B):页模型(状态栏 / 单页内容 / 按键提示行) -->
    <div v-else-if="isEink" class="paged">
      <div class="p-status">
        {{ connectionStore.connected ? "已连接" : "离线" }} · {{ pageIndex + 1 }}/{{ pageCount }}
      </div>
      <div class="p-content">
        <template v-if="currentPage?.kind === 'card'">
          <h1>{{ currentPage.card.title }}</h1>
          <p v-if="currentPage.card.body">{{ currentPage.card.body }}</p>
        </template>
        <template v-else-if="currentPage?.kind === 'briefing'">
          <h1>简报</h1>
          <ul>
            <li v-for="(item, i) in cardsStore.briefing?.items ?? []" :key="i">
              [{{ item.time ?? "全天" }}] {{ item.title }}
            </li>
          </ul>
        </template>
        <!-- 无卡:A 档身份页(姓名/部门/二维码),B 档空态(契约 §6.1) -->
        <template v-else-if="isA">
          <h1>虚拟工牌</h1>
          <p class="idname">张三</p>
          <p class="iddept">研发部</p>
          <div class="qr">二维码</div>
        </template>
        <p v-else class="p-empty">{{ connectionStore.connected ? "暂无待办" : "连接主机中…" }}</p>
      </div>
      <div class="p-hint">
        <button class="p-key" @click="nextPage">翻页</button>
        <button class="p-key" @click="toggleRecording">录音</button>
      </div>
    </div>

    <!-- 手机模式:顶对齐待办应用布局 -->
    <template v-else>
      <h1>今日</h1>
      <p v-if="connectionStore.state === 'reconnecting'" class="hint">连接中断,重连中…</p>
      <p v-else-if="connectionStore.state === 'offline_cached'" class="hint">
        已离线缓存,恢复后自动补传
      </p>
      <div v-if="cardsStore.briefing" class="briefing">
        <h2>简报 {{ cardsStore.briefing.date }}</h2>
        <ul>
          <li v-for="(item, i) in cardsStore.briefing.items" :key="i">
            [{{ item.time ?? "全天" }}] {{ item.title }}
          </li>
        </ul>
      </div>
      <ul v-if="cardsStore.cards.length" class="cards">
        <li v-for="card in sortedCards" :key="card.card_id">
          <label class="card-check">
            <input type="checkbox" @change="completeCard(card)" />
            <span class="card-title">{{ card.title }}</span>
          </label>
          <span v-if="card.body" class="card-body">{{ card.body }}</span>
        </li>
      </ul>
      <p v-if="!cardsStore.cards.length && !cardsStore.briefing" class="hint">
        暂无简报与提醒卡片
      </p>
      <p v-if="connectionStore.notice" class="notice">{{ connectionStore.notice }}</p>
      <button class="record" @click="toggleRecording">开始录音</button>
    </template>
  </section>
</template>

<style scoped>
.view {
  min-height: 100vh;
  background: #f5f5f5;
  color: #000;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
}
/* 手机模式:顶对齐响应式应用布局(电子纸档无 .phone 类,完全不受这些规则影响) */
.view.phone {
  justify-content: flex-start;
  align-items: stretch;
  max-width: 680px;
  margin: 0 auto;
  padding: 20px 16px;
  gap: 12px;
  text-align: left;
}
.cards {
  list-style: none;
  padding: 0;
  margin: 0;
}
.cards li {
  display: block;
  border: 1px solid #ddd;
  border-radius: 8px;
  background: #fff;
  padding: 10px 12px;
  margin: 8px 0;
}
.card-check {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  cursor: pointer;
}
.card-check input[type="checkbox"] {
  width: 20px;
  height: 20px;
  margin-top: 2px;
  flex-shrink: 0;
}
.card-title {
  flex: 1;
  font-size: 17px;
  line-height: 1.5;
  word-break: break-word;
}
.card-body {
  display: block;
  font-size: 14px;
  color: #555;
  margin-top: 4px;
  padding-left: 30px;
}
.briefing {
  font-size: 15px;
}
.briefing h2 {
  font-size: 17px;
  margin: 4px 0;
}
.briefing ul {
  padding-left: 20px;
  margin: 0;
  line-height: 1.7;
}
.hint {
  color: #555;
}
.notice {
  color: #d00;
}
.record {
  width: 200px;
  height: 56px;
  font-size: 20px;
  border: 2px solid #000;
  border-radius: 28px;
  background: #fff;
  color: #000;
  user-select: none;
  align-self: center;
}
.record:active {
  background: #000;
  color: #fff;
}
.result {
  text-align: center;
  padding: 0 24px;
}
.preview-input {
  display: block;
  width: 100%;
  box-sizing: border-box;
  font-size: 16px;
  padding: 8px 10px;
  margin: 6px 0;
  border: 1px solid #999;
  border-radius: 6px;
  text-align: left;
}
.preview-actions {
  display: flex;
  gap: 8px;
  justify-content: center;
  margin-top: 8px;
}
.candidate {
  display: block;
  margin: 8px auto;
  padding: 10px 24px;
  font-size: 18px;
  border: 1px solid #000;
  border-radius: 8px;
  background: #fff;
}
.preview-actions .candidate {
  display: inline-block;
  margin: 0;
}
/* 页模型(电子纸档):状态栏 / 单页内容 / 按键提示行 */
.paged {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
}
.p-content {
  flex: 1;
}
.p-content ul {
  list-style: none;
  padding: 0;
  margin: 0;
}
.p-hint {
  display: flex;
  gap: 8px;
}
</style>
