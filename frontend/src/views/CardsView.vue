<script setup lang="ts">
// 卡片页(08 §6.1 页面模型):
//   默认页 = 当前最高优先 active 卡(单卡,不与简报堆叠);简报页 = 翻页进入的独立页;
//   无卡时 A 档显示身份页、B 档显示空态;结果(intent.result)与 clarify 全屏覆盖;
//   手机模式保持平铺(演示端)。
// 优先级规则(自定,已注明):remind_at 最近者优先(催办语义),无 remind_at 排最后,同刻按创建序。
import { computed, ref } from "vue";

import { toggleRecording } from "../lib/recording";
import { wsClient } from "../lib/ws-client";
import type { Card, ClarifyCandidate } from "../protocol/messages";
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
</script>

<template>
  <section class="view">
    <!-- 结果页 / clarify 候选页:全屏覆盖,≤3s 返回原页面(页序号保持) -->
    <div v-if="connectionStore.lastResult" class="result">
      <h1>{{ connectionStore.lastResult.title }}</h1>
      <p v-if="connectionStore.lastResult.body">{{ connectionStore.lastResult.body }}</p>
      <template v-if="connectionStore.lastResult.status === 'clarify'">
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
          <p class="iddept">研发部(占位)</p>
          <div class="qr">二维码</div>
        </template>
        <p v-else class="p-empty">{{ connectionStore.connected ? "暂无待办" : "连接主机中…" }}</p>
      </div>
      <div class="p-hint">
        <button class="p-key" @click="nextPage">翻页</button>
        <button class="p-key" @click="toggleRecording">录音</button>
      </div>
    </div>

    <!-- 手机模式:平铺(演示端) -->
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
          <strong>{{ card.title }}</strong>
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
.cards {
  list-style: none;
  padding: 0;
  margin: 0;
  font-size: 18px;
  line-height: 1.8;
}
.card-body {
  display: block;
  font-size: 14px;
}
.briefing {
  font-size: 16px;
}
.briefing h2 {
  font-size: 18px;
  margin: 4px 0;
}
.briefing ul {
  list-style: none;
  padding: 0;
  margin: 0;
  line-height: 1.8;
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
}
.record:active {
  background: #000;
  color: #fff;
}
.result {
  text-align: center;
  padding: 0 24px;
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
