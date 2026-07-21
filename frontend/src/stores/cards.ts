/**
 * 卡片 store:active 提醒卡片与当日简报(屏幕内容白名单见宪法第 4 条)。
 * 用 vue reactive,不引入 Pinia(08 §2;规约 §7)。
 */
import { reactive } from "vue";

import type { BriefPushPayload, Card, DismissReason } from "../protocol/messages";

export const cardsStore = reactive({
  /** 全部 active 卡片(state.sync 全量下发,reminder.push 增量更新) */
  cards: [] as Card[],
  /** 当日简报,无则 null */
  briefing: null as BriefPushPayload | null,
  /** state.sync 全量替换(重连后恢复的唯一入口,08 §1.1) */
  replaceAll(cards: Card[]): void {
    this.cards = [...cards];
  },
  /** reminder.push / state.sync 时更新或插入卡片 */
  upsertCard(card: Card): void {
    const i = this.cards.findIndex((c) => c.card_id === card.card_id);
    if (i >= 0) {
      this.cards[i] = card;
    } else {
      this.cards.push(card);
    }
  },
  /** reminder.dismiss 时撤下卡片 */
  dismissCard(cardId: string, _reason: DismissReason): void {
    this.cards = this.cards.filter((c) => c.card_id !== cardId);
  },
  /** brief.push 时更新当日简报 */
  setBriefing(briefing: BriefPushPayload): void {
    this.briefing = briefing;
  },
});
