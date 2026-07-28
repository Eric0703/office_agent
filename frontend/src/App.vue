<script setup lang="ts">
// 壳:?desk=1 → PC 草稿工作台(只读,不连 WS);否则按端侧状态机切换四个 view(08 §1.1/§2)
// ?eink=a|b:电子纸仿真两档(08 §6);方案 A 下 A/B 仅是屏幕档位,均有身份首页
// eink 档画布下方挂虚拟物理键区(.hw-keys):三枚物理键统一经 device-input.pressKey,
// 与未来 ESP32 GPIO 按键同语义(虚拟工牌硬件模拟);主操作键 action 支持短按/长按(≥600ms)
import { computed } from "vue";

import { pressKey, type DeviceKey } from "./lib/device-input";
import { connectionStore } from "./stores/connection";
import { uiStore } from "./stores/ui";
import CardsView from "./views/CardsView.vue";
import ConfirmView from "./views/ConfirmView.vue";
import DeskView from "./views/DeskView.vue";
import IdentityView from "./views/IdentityView.vue";
import RecordingView from "./views/RecordingView.vue";

const isDesk = new URLSearchParams(window.location.search).has("desk");
const isB = computed(() => uiStore.eink === "b");

const currentView = computed(() => {
  switch (connectionStore.state) {
    case "unpaired":
    case "pairing":
      // B 档配对等待维持现状:在卡片页呈现(配对流不动;显示契约 §6.1)
      return isB.value ? CardsView : IdentityView;
    case "recording":
    case "uploading":
    case "processing":
      return RecordingView;
    case "confirm_wait":
      return ConfirmView;
    default:
      // idle / showing / reconnecting / offline_cached 均以卡片页为底
      return CardsView;
  }
});

// 标注竖向逻辑画布(08 §6.3):A/B 统一 300×400,B 档仅协议档位差异
const frameLabel = computed(
  () => `电子纸仿真 300×400 竖向(Profile ${isB.value ? "B" : "A"})`,
);

/** 虚拟物理键(语义与 ESP32 GPIO 一致):主操作键(录音)/ 上翻 / 下翻 */
const HW_KEYS: ReadonlyArray<{ key: DeviceKey; label: string }> = [
  { key: "action", label: "录音" },
  { key: "page_up", label: "上翻" },
  { key: "page_down", label: "下翻" },
];

/** 按键按状态禁用(规则从简):未配对/配对中全禁;录音中仅主操作键;上传/处理中全禁;
 *  其余(idle/showing/confirm_wait/reconnecting/offline_cached)全可用——
 *  clarify/确认页/结果页的 action 复用语义在 device-input/状态机内按状态兜底 */
function keyEnabled(key: DeviceKey): boolean {
  switch (connectionStore.state) {
    case "unpaired":
    case "pairing":
    case "uploading":
    case "processing":
      return false;
    case "recording":
      return key === "action";
    default:
      return true;
  }
}

/* ---------- 主操作键手势:短按/长按(≥600ms)语义由 device-input 按界面状态分派 ---------- */
const LONG_PRESS_MS = 600;
let longPressTimer: number | undefined;
let longFired = false;

/** 翻页键(上翻/下翻):只有短按语义(单击零延迟,双击不实现) */
function onKeyClick(key: DeviceKey): void {
  if (key !== "action") {
    pressKey(key);
  }
}

function onActionDown(): void {
  longFired = false;
  window.clearTimeout(longPressTimer);
  longPressTimer = window.setTimeout(() => {
    longFired = true;
    pressKey("action", "long");
  }, LONG_PRESS_MS);
}

/** 超时前释放(pointerup/pointerleave)= 短按;超时已触发长按则不再补短按 */
function onActionRelease(): void {
  window.clearTimeout(longPressTimer);
  if (!longFired) {
    pressKey("action", "short");
  }
  longFired = false;
}
</script>

<template>
  <DeskView v-if="isDesk" />
  <div v-else-if="uiStore.eink !== 'off'" class="eink-frame">
    <p class="eink-label">
      {{ frameLabel }} · <a href="?eink=a">A</a> / <a href="?eink=b">B</a> /
      <a href="/">关闭</a>
    </p>
    <div class="eink-canvas">
      <component :is="currentView" />
    </div>
    <!-- 硬件控制区:画布外虚拟物理键(屏幕无触屏,三键承担全部输入);
         主操作键走 pointer 事件实现长按,上翻/下翻键只短按(click) -->
    <div class="hw-keys">
      <button
        v-for="k in HW_KEYS"
        :key="k.key"
        type="button"
        class="hw-key"
        :data-key="k.key"
        :disabled="!keyEnabled(k.key)"
        @click="onKeyClick(k.key)"
        @pointerdown="k.key === 'action' && onActionDown()"
        @pointerup="k.key === 'action' && onActionRelease()"
        @pointerleave="k.key === 'action' && onActionRelease()"
      >
        {{ k.label }}
      </button>
    </div>
  </div>
  <template v-else>
    <component :is="currentView" />
    <a class="eink-entry" href="?eink=a">仿真A</a>
    <a class="eink-entry entry-b" href="?eink=b">仿真B</a>
  </template>
</template>

<style>
body {
  margin: 0;
  background: #f5f5f5;
  color: #000;
  font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif;
}
</style>
