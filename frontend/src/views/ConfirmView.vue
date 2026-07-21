<script setup lang="ts">
// 确认页:L2 风险操作物理确认(宪法第 5 条;登记册 §2.3 confirm.request/response)
import { wsClient } from "../lib/ws-client";
import { DeviceEvent } from "../state/machine";
import { connectionStore } from "../stores/connection";

function respond(decision: "confirm" | "cancel"): void {
  const pending = connectionStore.pendingConfirm;
  if (!pending) {
    return;
  }
  wsClient.send("confirm.response", { confirm_id: pending.confirm_id, decision });
  connectionStore.pendingConfirm = null;
  connectionStore.dispatch(DeviceEvent.ConfirmResolved);
}
</script>

<template>
  <section class="view">
    <h1>{{ connectionStore.pendingConfirm?.title ?? "确认操作" }}</h1>
    <p v-if="connectionStore.pendingConfirm?.body">{{ connectionStore.pendingConfirm.body }}</p>
    <div class="actions">
      <button class="primary" @click="respond('confirm')">确认</button>
      <button @click="respond('cancel')">取消</button>
    </div>
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
