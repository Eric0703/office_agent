<script setup lang="ts">
// 身份页:身份信息(宪法第 4 条白名单)+ 配对状态(08 §2 views.Identity)
// 正式配对(FR-01):未配对时展示 6 位配对码,等 Owner 在电脑端批准;
// 原型期 dev_mode=auto_approve:连接即直通,本页只是一闪而过的状态提示
import { computed } from "vue";

import { connectionStore } from "../stores/connection";

const statusText = computed(() => {
  if (connectionStore.pairCode) {
    return "请在电脑端批准配对码";
  }
  return connectionStore.state === "pairing" ? "连接主机中…" : "未配对";
});
</script>

<template>
  <section class="view">
    <h1>虚拟工牌</h1>
    <p class="name">张三</p>
    <p class="dept">研发部</p>
    <div class="qr">二维码</div>
    <p v-if="connectionStore.pairCode" class="paircode">{{ connectionStore.pairCode }}</p>
    <p class="status">{{ statusText }}</p>
    <p v-if="connectionStore.notice" class="status">{{ connectionStore.notice }}</p>
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
}
.name {
  font-size: 24px;
  font-weight: bold;
  margin: 8px 0 0;
}
.dept {
  margin: 4px 0;
}
.qr {
  width: 64px;
  height: 64px;
  border: 2px solid #000;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  margin: 8px 0;
}
.paircode {
  font-size: 40px;
  font-weight: bold;
  letter-spacing: 8px;
  margin: 12px 0 4px;
}
.status {
  color: #555;
  font-size: 14px;
}
</style>
