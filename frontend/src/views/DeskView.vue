<script setup lang="ts">
// PC 草稿工作台(?desk=1):本机查看最近处理记录与待确认草稿(Gate 0 可观察性入口)。
// 数据来自同源接口 /desk/records、/desk/drafts、/desk/tasks;不上传任何内容出本机;
// 笔记草稿可经人工确认归档到本机笔记目录(FR-05);待办转任务草稿尚未实现。
import { onBeforeUnmount, onMounted, reactive, ref } from "vue";

interface DeskRecord {
  created_at: string;
  status: string;
  transcript: string | null;
  confidence: number | null;
}

interface DeskDraft {
  id: string;
  kind: "note" | "experience";
  created_at: string;
  content_md: string;
  status: string;
}

interface DeskTask {
  id: string;
  kind: "task" | "timer";
  title: string;
  time: string | null;
  status: string;
  created_at: string;
}

const desk = reactive({
  records: [] as DeskRecord[],
  drafts: [] as DeskDraft[],
  tasks: [] as DeskTask[],
  loadError: "",
  updatedAt: "",
});

const KIND_LABEL: Record<string, string> = {
  note: "现场记录",
  experience: "经验卡片",
};

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

async function refresh(): Promise<void> {
  try {
    const [recordsResp, draftsResp, tasksResp] = await Promise.all([
      fetch("/desk/records"),
      fetch("/desk/drafts"),
      fetch("/desk/tasks"),
    ]);
    if (!recordsResp.ok || !draftsResp.ok || !tasksResp.ok) {
      throw new Error("bad response");
    }
    desk.records = (await recordsResp.json()) as DeskRecord[];
    desk.drafts = (await draftsResp.json()) as DeskDraft[];
    desk.tasks = (await tasksResp.json()) as DeskTask[];
    desk.loadError = "";
    desk.updatedAt = new Date().toLocaleTimeString();
  } catch {
    desk.loadError = "无法读取本机数据,请确认 Agent 主机已启动";
  }
}

let timer: number | undefined;
onMounted(() => {
  void refresh();
  timer = window.setInterval(() => void refresh(), 5_000);
});
onBeforeUnmount(() => window.clearInterval(timer));

/** 勾选完成待办:与工牌勾选同一接口,完成后卡片同步撤下(FR-07 闭环) */
async function completeTask(t: DeskTask): Promise<void> {
  await fetch(`/desk/tasks/${t.id}/complete`, { method: "POST" });
  await refresh();
}

/** 取消一次性定时提醒(timer 卡撤下) */
async function cancelReminder(t: DeskTask): Promise<void> {
  await fetch(`/desk/reminders/${t.id}/cancel`, { method: "POST" });
  await refresh();
}

/** 人工确认归档笔记草稿(FR-05):请求期间防重复点击;不回显本机文件路径 */
const confirming = ref<Set<string>>(new Set());
const notice = reactive({ ok: "", error: "" });

async function confirmDraft(d: DeskDraft): Promise<void> {
  if (confirming.value.has(d.id)) return;
  confirming.value.add(d.id);
  notice.ok = "";
  notice.error = "";
  try {
    const resp = await fetch(`/desk/drafts/${d.id}/confirm`, { method: "POST" });
    if (resp.ok) {
      notice.ok = "草稿已归档到本机笔记目录";
    } else {
      notice.error = "归档未成功,请稍后重试";
    }
  } catch {
    notice.error = "归档未成功,请稍后重试";
  } finally {
    confirming.value.delete(d.id);
    await refresh();
  }
}
</script>

<template>
  <main class="desk">
    <h1>草稿工作台</h1>
    <p class="meta">
      仅本机数据,每 5 秒自动刷新<template v-if="desk.updatedAt">
        · 上次更新 {{ desk.updatedAt }}</template
      >
    </p>
    <p v-if="desk.loadError" class="error">{{ desk.loadError }}</p>

    <section>
      <h2>最近处理记录</h2>
      <p v-if="!desk.records.length" class="empty">暂无处理记录</p>
      <ul v-else class="records">
        <li v-for="(r, i) in desk.records" :key="i">
          <div class="row">
            <span class="time">{{ fmtTime(r.created_at) }}</span>
            <span class="status">{{ r.status }}</span>
            <span v-if="r.confidence !== null" class="conf">
              识别置信度 {{ Math.round(r.confidence * 100) }}%
            </span>
          </div>
          <p v-if="r.transcript" class="transcript">{{ r.transcript }}</p>
        </li>
      </ul>
    </section>

    <section>
      <h2>待办任务 / 提醒</h2>
      <p v-if="!desk.tasks.length" class="empty">暂无待办任务与提醒</p>
      <ul v-else class="records">
        <li v-for="(t, i) in desk.tasks" :key="i">
          <div class="row">
            <span class="kind">{{ t.kind === "timer" ? "定时提醒" : "待办" }}</span>
            <span>{{ t.title }}</span>
            <span class="conf">{{ t.status }}</span>
            <button
              v-if="t.kind === 'task' && t.status === '未完成'"
              class="action"
              @click="completeTask(t)"
            >
              完成
            </button>
            <button
              v-if="t.kind === 'timer' && t.status === '生效中'"
              class="action"
              @click="cancelReminder(t)"
            >
              取消
            </button>
          </div>
          <div class="row meta">
            <span v-if="t.time">时间:{{ fmtTime(t.time) }}</span>
            <span class="time">创建:{{ fmtTime(t.created_at) }}</span>
          </div>
        </li>
      </ul>
    </section>

    <section>
      <h2>待确认草稿</h2>
      <p class="meta">草稿经人工确认后归档到本机笔记目录;待办转任务草稿尚未实现。</p>
      <p v-if="notice.ok" class="notice-ok">{{ notice.ok }}</p>
      <p v-if="notice.error" class="error">{{ notice.error }}</p>
      <p v-if="!desk.drafts.length" class="empty">暂无待确认草稿</p>
      <article v-for="(d, i) in desk.drafts" :key="i" class="draft">
        <header>
          <span class="kind">{{ KIND_LABEL[d.kind] ?? d.kind }}</span>
          <span class="time">{{ fmtTime(d.created_at) }}</span>
          <button
            v-if="d.kind === 'note'"
            class="action"
            :disabled="confirming.has(d.id)"
            @click="confirmDraft(d)"
          >
            确认归档
          </button>
          <span class="pending">待确认</span>
        </header>
        <pre class="content">{{ d.content_md }}</pre>
      </article>
    </section>
  </main>
</template>

<style scoped>
.desk {
  max-width: 720px;
  margin: 0 auto;
  padding: 16px;
  font-size: 15px;
}
.meta {
  color: #555;
  font-size: 13px;
}
.error {
  color: #d00;
}
.notice-ok {
  color: #080;
  font-size: 13px;
}
.empty {
  color: #888;
}
.records {
  list-style: none;
  padding: 0;
  margin: 0;
}
.records li {
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 8px 12px;
  margin: 8px 0;
  background: #fff;
}
.row {
  display: flex;
  gap: 12px;
  align-items: baseline;
}
.time {
  color: #555;
  font-size: 13px;
}
.status {
  font-weight: bold;
}
.conf {
  margin-left: auto;
  color: #555;
  font-size: 13px;
}
.action {
  font-size: 13px;
  padding: 2px 10px;
  border: 1px solid #888;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
}
.transcript {
  margin: 6px 0 0;
}
.draft {
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 8px 12px;
  margin: 8px 0;
  background: #fff;
}
.draft header {
  display: flex;
  gap: 12px;
  align-items: baseline;
  border-bottom: 1px solid #eee;
  padding-bottom: 4px;
}
.kind {
  font-weight: bold;
}
.pending {
  margin-left: auto;
  color: #d00;
  font-size: 13px;
}
.content {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  margin: 8px 0 0;
}
</style>
