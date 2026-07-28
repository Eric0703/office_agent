<script setup lang="ts">
// 卡片页(方案 A 单屏 AI 工牌;08 §6.1 页面模型):
//   页序列 = 身份首页(0,已连接默认页,有无待办均在) → 待办/提醒卡(多张,单卡一页) → 简报页;
//   上翻/下翻双向循环(device-input.turnPage);结果(intent.result)与 clarify 全屏覆盖;
//   手机模式 = 顶对齐待办应用布局(触控勾选完成、多任务可编辑预览),与电子纸档样式隔离。
// 输入统一经 device-input.pressKey(虚拟工牌硬件模拟,三键):画布内不放录音/翻页触摸按钮,
// 电子纸档候选纯展示+高亮(上翻/下翻移动、短按主操作键选定、长按取消);手机档按钮点击调同一组动作函数。
// 布局规则(电子纸档):身份首页信息组水平+垂直居中且文字居中;其余页内容组垂直居中、文字左对齐。
// 优先级规则(自定,已注明):remind_at 最近者优先(催办语义),无 remind_at 排最后,同刻按创建序。
import { computed } from "vue";

import {
  cancelPreview,
  confirmPreview,
  dismissResult,
  isTaskPreview,
  pick,
  pressKey,
  previewTasks,
} from "../lib/device-input";
import type { Card, ClarifyCandidate } from "../protocol/messages";
import { cardsStore } from "../stores/cards";
import { connectionStore } from "../stores/connection";
import { uiStore } from "../stores/ui";
import BadgeIdentity from "./BadgeIdentity.vue";

const isEink = computed(() => uiStore.eink !== "off");
const isB = computed(() => uiStore.eink === "b");

/* ---------- 页序列(方案 A):身份首页 + 单卡 ×N + 简报页,上翻/下翻双向循环(device-input.turnPage) ---------- */

type Page = { kind: "identity" } | { kind: "card"; card: Card } | { kind: "briefing" };

/** 最高优先排序:remind_at 最近优先,无则最后(字符串序即时间序) */
const sortedCards = computed<Card[]>(() =>
  [...cardsStore.cards].sort((a, b) =>
    (a.remind_at ?? "9999") < (b.remind_at ?? "9999") ? -1 : 1,
  ),
);

const pages = computed<Page[]>(() => [
  { kind: "identity" }, // 方案 A:身份恒为页 0(默认首页),A/B 档一致(B 仅是屏幕档位)
  ...sortedCards.value.map((card) => ({ kind: "card", card }) as Page),
  ...(cardsStore.briefing ? [{ kind: "briefing" } as Page] : []),
]);

const pageCount = computed(() => Math.max(pages.value.length, 1));

const pageIndex = computed(() => Math.min(uiStore.cardPage, pageCount.value - 1));

const currentPage = computed<Page | undefined>(() => pages.value[pageIndex.value]);

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

/* ---------- clarify:全屏展示;B 档 ≤2 个候选/屏,可见批次随高亮(上翻/下翻键)移动 ---------- */

/** B 档可见批次的起始绝对序号:每屏 2 个,批次随高亮翻页(等效原"下一批") */
const clarifySliceStart = computed(() => {
  const all = connectionStore.lastResult?.candidates ?? [];
  if (uiStore.eink !== "b" || all.length === 0) {
    return 0;
  }
  const index = Math.min(uiStore.clarifyIndex, all.length - 1);
  return Math.floor(index / 2) * 2;
});

const clarifySlice = computed<ClarifyCandidate[]>(() => {
  const all = connectionStore.lastResult?.candidates ?? [];
  if (uiStore.eink !== "b") {
    return all;
  }
  return all.slice(clarifySliceStart.value, clarifySliceStart.value + 2);
});

/* ---------- 结果页:失败/未听清停留,由短按主操作键(手机点击)关闭;成功 3s 自动返回(08 §6.1) ---------- */

const isFailedResult = computed(
  () =>
    connectionStore.lastResult !== null &&
    ["failed", "low_confidence"].includes(connectionStore.lastResult.status),
);
</script>

<template>
  <section class="view" :class="{ phone: !isEink }">
    <!-- 结果页 / clarify 候选页:全屏覆盖;成功 3s 自动返回,失败/未听清停留至关闭(页序号保持) -->
    <div v-if="connectionStore.lastResult" class="result" @click="dismissResult">
      <h1>{{ connectionStore.lastResult.title }}</h1>
      <p v-if="connectionStore.lastResult.body && !isTaskPreview">
        {{ connectionStore.lastResult.body }}
      </p>
      <p v-if="isFailedResult" class="hint">{{ isEink ? "短按关闭" : "点击返回" }}</p>
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
        <!-- 电子纸档:纯展示+高亮(贴近无触屏硬件;上翻/下翻移动,短按选定,长按取消) -->
        <template v-if="isEink">
          <div
            v-for="(c, i) in clarifySlice"
            :key="c.candidate_id"
            class="candidate-item"
            :class="{ hl: clarifySliceStart + i === uiStore.clarifyIndex }"
          >
            {{ clarifySliceStart + i === uiStore.clarifyIndex ? "► " : "" }}{{ c.label }}
          </div>
          <p class="hint">上翻/下翻移动 · 短按选定 · 长按取消</p>
        </template>
        <!-- 手机模式:候选可点(描边高亮;点击等效主操作键短按选定该候选) -->
        <template v-else>
          <button
            v-for="(c, i) in clarifySlice"
            :key="c.candidate_id"
            class="candidate"
            :class="{ hl: i === uiStore.clarifyIndex }"
            @click="pick(c.candidate_id)"
          >
            {{ c.label }}
          </button>
        </template>
      </template>
    </div>

    <!-- 电子纸档(A/B):页模型(状态栏 / 单页内容 / 按键提示行) -->
    <div v-else-if="isEink" class="paged">
      <div class="p-status">
        {{ connectionStore.connected ? "已连接" : "离线" }} · {{ pageIndex + 1 }}/{{ pageCount }}
      </div>
      <div class="p-content">
        <!-- 页 0:身份首页(方案 A 默认页;与 IdentityView 共享 BadgeIdentity 片段,不含配对码);
             信息组水平+垂直居中、文字与二维码居中;
             B 档配对等待维持现状:未连接时仍显示空态提示(配对流不动) -->
        <div v-if="currentPage?.kind === 'identity'" class="p-group p-identity">
          <p v-if="isB && !connectionStore.connected" class="p-empty">连接主机中…</p>
          <BadgeIdentity v-else />
        </div>
        <!-- 其余页:内容组垂直居中、文字左对齐 -->
        <div v-else-if="currentPage?.kind === 'card'" class="p-group">
          <h1>{{ currentPage.card.title }}</h1>
          <p v-if="currentPage.card.body">{{ currentPage.card.body }}</p>
        </div>
        <div v-else-if="currentPage?.kind === 'briefing'" class="p-group">
          <h1>简报</h1>
          <ul>
            <li v-for="(item, i) in cardsStore.briefing?.items ?? []" :key="i">
              [{{ item.time ?? "全天" }}] {{ item.title }}
            </li>
          </ul>
        </div>
      </div>
      <!-- 按键提示行:纯文本(物理键在画布外 .hw-keys,画布内不放触摸按钮) -->
      <div class="p-hint">
        <span class="p-hint-text">录音键开始 · 上翻/下翻切换</span>
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
      <button class="record" @click="pressKey('action')">开始录音</button>
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
/* 手机档候选高亮(与主操作键选定联动):描边 */
.candidate.hl {
  outline: 3px solid #000;
}
.preview-actions .candidate {
  display: inline-block;
  margin: 0;
}
/* 电子纸档候选:纯展示(无点击);高亮反白样式见 eink.css(画布纯黑白规则) */
.candidate-item {
  margin: 8px auto;
  padding: 8px 12px;
  font-size: 18px;
  border: 1px solid #000;
  max-width: 240px;
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
