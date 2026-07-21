<script setup lang="ts">
// 录音页:静态"录音中" + 黑条 + "点击结束"(08 §6.1;Owner 决策:取消每秒计时,
// 录音中不刷新,即时反馈由 LED+震动承担;满 3 分钟局刷一次提示,满 5 分钟自动停止)
import { computed } from "vue";

import { recordingUi, toggleRecording } from "../lib/recording";
import { connectionStore } from "../stores/connection";

const stateText = computed(() => {
  switch (connectionStore.state) {
    case "recording":
      return "录音中";
    case "uploading":
      return "上传中…";
    case "processing":
      return "识别处理中…";
    default:
      return "";
  }
});
</script>

<template>
  <section class="view">
    <div v-if="connectionStore.state === 'recording'" class="rec-bar" />
    <h1>{{ stateText }}</h1>
    <template v-if="connectionStore.state === 'recording'">
      <p v-if="recordingUi.reminded" class="notice">已录制 3 分钟</p>
      <button class="stop" @click="toggleRecording">点击结束</button>
    </template>
    <p v-if="connectionStore.notice" class="notice">{{ connectionStore.notice }}</p>
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
.rec-bar {
  width: 80vw;
  height: 12px;
  border-radius: 6px;
  background: #d00;
  animation: pulse 1s infinite alternate;
}
.notice {
  color: #d00;
}
.stop {
  width: 200px;
  height: 56px;
  font-size: 20px;
  border: 2px solid #d00;
  border-radius: 28px;
  background: #fff;
  color: #d00;
  user-select: none;
}
.stop:active {
  background: #d00;
  color: #fff;
}
@keyframes pulse {
  from {
    opacity: 1;
  }
  to {
    opacity: 0.4;
  }
}
</style>
