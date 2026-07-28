/**
 * UI 偏好 store:电子纸仿真档位(08 §6 两档显示契约)与 B 档翻页状态。
 * ?eink=a → Profile A;?eink=b → Profile B(布局与 A 一致,仅协议档位差异);
 * ?eink=1(历史用法)按 A 处理;无参数 = 普通手机模式。
 */
import { reactive } from "vue";

export type EinkProfile = "off" | "a" | "b";

function detectProfile(): EinkProfile {
  const v = new URLSearchParams(window.location.search).get("eink");
  if (v === "b") {
    return "b";
  }
  if (v !== null) {
    return "a";
  }
  return "off";
}

/** 端侧画布档位对应的协议声明值(登记册 §2.1 display_profile)。
 * 语义为屏幕模组档位(400×300 / 296×128 模组);仿真画布 A/B 统一 300×400 竖向(08 §6.3),
 * 296×128 仅为 2.9" 模组物理参数。 */
export function displayProfile(): "400x300" | "296x128" {
  return uiStore.eink === "b" ? "296x128" : "400x300";
}

export const uiStore = reactive({
  eink: detectProfile() as EinkProfile,
  /** 方案 A 页模型的当前页序号(0 = 身份首页;身份 → 卡片 → 简报,上翻/下翻双向循环) */
  cardPage: 0,
  /** clarify 候选的当前高亮序号(0 起;上翻/下翻键 ±1 双向循环,新 clarify 到达时归零) */
  clarifyIndex: 0,
});
