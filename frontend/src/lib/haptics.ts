/**
 * 震动封装:reminder.push / brief.push 到达与 confirm.request 的触觉提示(08 §1.1)。
 */
export function vibrate(pattern: VibratePattern = 30): boolean {
  return "vibrate" in navigator && navigator.vibrate(pattern);
}
