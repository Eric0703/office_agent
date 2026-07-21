<script setup lang="ts">
// 壳:?desk=1 → PC 草稿工作台(只读,不连 WS);否则按端侧状态机切换四个 view(08 §1.1/§2)
// ?eink=a|b:电子纸仿真两档(08 §6);B 档无身份页(身份由实体工卡承担)
import { computed } from "vue";

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
      // B 档无身份页:配对等待也在卡片页呈现(显示契约 §6.1)
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
const frameLabel = computed(() =>
  `电子纸仿真 300×400 竖向(Profile ${isB.value ? "B" : "A"})`,
);
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
