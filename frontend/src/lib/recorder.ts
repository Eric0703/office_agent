/**
 * 按住说话录音(MediaRecorder 封装)。
 * 宪法第 2 条:录音唯一合法触发是人有意识地按下按键,禁止任何自动/后台录音。
 */
export class BadgeRecorder {
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];

  /** 按下:申请麦克风并开始录音 */
  async start(): Promise<void> {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.chunks = [];
    this.recorder = new MediaRecorder(stream);
    this.recorder.ondataavailable = (e: BlobEvent) => {
      if (e.data.size > 0) {
        this.chunks.push(e.data);
      }
    };
    this.recorder.start();
  }

  /**
   * 松开:等 stop 事件收齐末块后返回音频 Blob(上传走 HTTP POST /audio/{record_id})。
   * duration_ms < 2000 的误触由调用方判定并静默丢弃(协议 §2.2;08 §1.1)。
   */
  async stop(): Promise<Blob> {
    const recorder = this.recorder;
    if (!recorder) {
      return new Blob();
    }
    const mimeType = recorder.mimeType || "audio/webm";
    await new Promise<void>((resolve) => {
      recorder.onstop = () => resolve();
      recorder.stop();
    });
    recorder.stream.getTracks().forEach((t) => t.stop());
    this.recorder = null;
    return new Blob(this.chunks, { type: mimeType });
  }
}
