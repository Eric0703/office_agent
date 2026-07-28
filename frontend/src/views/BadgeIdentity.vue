<script setup lang="ts">
// 员工身份信息共享片段(宪法第 4 条白名单):姓名/部门/二维码。
// 方案 A:已连接默认首页 = 身份(CardsView 页 0)与配对等待页(IdentityView)复用同一片段,
// 不复制两份文案;配对码不属于本片段(仅配对流程在 IdentityView 追加展示)。
// 样式:此处为手机档基准;eink 画布内由 eink.css(.eink-canvas .p-content …)按契约覆盖。
</script>

<template>
  <h1>虚拟工牌</h1>
  <p class="idname">张三</p>
  <p class="iddept">研发部</p>
  <div class="qr">二维码</div>
</template>

<style scoped>
.idname {
  font-size: 24px;
  font-weight: bold;
  margin: 8px 0 0;
}
.iddept {
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
</style>
