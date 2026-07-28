<script setup lang="ts">
// 确认页:L2 风险操作物理确认(宪法第 5 条;登记册 §2.3 confirm.request/response)
// 电子纸档无触屏:确认/取消由画布外主操作键承担(长按=确认执行,短按=取消),画布内只给文本提示
import { computed } from "vue";

import { pressKey } from "../lib/device-input";
import { connectionStore } from "../stores/connection";
import { uiStore } from "../stores/ui";

const isEink = computed(() => uiStore.eink !== "off");
</script>

<template>
  <section class="view confirm">
    <h1>{{ connectionStore.pendingConfirm?.title ?? "确认操作" }}</h1>
    <p v-if="connectionStore.pendingConfirm?.body">{{ connectionStore.pendingConfirm.body }}</p>
    <div v-if="!isEink" class="actions">
      <button class="primary" @click="pressKey('action', 'long')">确认</button>
      <button @click="pressKey('action')">取消</button>
    </div>
    <p v-else class="hint">长按确认执行 · 短按取消</p>
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
  padding: 0 24px;
  text-align: center;
}
.actions {
  display: flex;
  gap: 16px;
}
button {
  padding: 12px 32px;
  font-size: 18px;
  border: 2px solid #000;
  border-radius: 8px;
  background: #fff;
}
button.primary {
  background: #000;
  color: #fff;
}
</style>
