import { createApp } from "vue";

import App from "./App.vue";
import { wsClient } from "./lib/ws-client";
import { uiStore } from "./stores/ui";
import "./styles/eink.css";

// 电子纸仿真模式:?eink=a|b 时根元素打标,样式见 styles/eink.css(08 §6 两档)
if (uiStore.eink !== "off") {
  document.documentElement.classList.add("eink", `eink-${uiStore.eink}`);
}

// PC 草稿工作台(?desk=1):只读页面,不建立工牌 WS 通道
const isDesk = new URLSearchParams(window.location.search).has("desk");

// 启动即连主机:hello 认证 → state.sync 恢复卡片(08 §1.1)
if (!isDesk) {
  wsClient.connect();
}

createApp(App).mount("#app");
