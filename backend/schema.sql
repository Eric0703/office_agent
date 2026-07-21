-- AI 工牌 Agent 主机 — 数据模型 v1.0
-- 依据 docs/08-架构设计.md §3;SQLite 3。
-- 约定:时间一律 ISO8601 文本(UTC);枚举以 TEXT + CHECK 约束表达;
-- audit_log 为 append-only,应用层禁止 UPDATE/DELETE。

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS devices (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  token_hash    TEXT NOT NULL,
  paired_at     TEXT NOT NULL,
  revoked_at    TEXT,
  last_seen_at  TEXT
);

CREATE TABLE IF NOT EXISTS records (
  id              TEXT PRIMARY KEY,          -- record_id,端侧生成,幂等键
  device_id       TEXT NOT NULL REFERENCES devices(id),
  mode            TEXT NOT NULL CHECK (mode IN ('auto','field','experience')),
  started_at      TEXT NOT NULL,
  duration_ms     INTEGER NOT NULL,
  audio_tmp_path  TEXT,                       -- 转写后即删并置 NULL(宪法第 3 条)
  status          TEXT NOT NULL CHECK (status IN
                    ('uploaded','transcribed','routed','done','failed')),
  transcript      TEXT,
  confidence      REAL,
  intent          TEXT,
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id            TEXT PRIMARY KEY,
  source        TEXT NOT NULL DEFAULT 'mock',   -- 'mock' 或适配器名
  source_id     TEXT,                            -- 外部系统主键映射
  title         TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done')),
  due_at        TEXT,
  completed_via TEXT CHECK (completed_via IN ('voice','pc') OR completed_via IS NULL),
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_events (
  id         TEXT PRIMARY KEY,
  source     TEXT NOT NULL DEFAULT 'mock',
  source_id  TEXT,
  title      TEXT NOT NULL,
  start_at   TEXT NOT NULL,
  end_at     TEXT NOT NULL,
  location   TEXT
);

CREATE TABLE IF NOT EXISTS cards (
  id             TEXT PRIMARY KEY,
  kind           TEXT NOT NULL CHECK (kind IN ('task','timer')),  -- 仅两类(宪法第 4 条)
  ref_task_id    TEXT REFERENCES tasks(id),
  title          TEXT NOT NULL,
  body           TEXT,
  status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','dismissed')),
  dismiss_reason TEXT CHECK (dismiss_reason IN ('completed','cancelled','expired')
                   OR dismiss_reason IS NULL),
  remind_at      TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);

CREATE TABLE IF NOT EXISTS briefings (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  date         TEXT NOT NULL,               -- YYYY-MM-DD
  content_json TEXT NOT NULL,               -- 条目含来源 task/event id,可追溯(FR-06)
  generated_at TEXT NOT NULL,
  pushed_at    TEXT,
  UNIQUE (date)
);

CREATE TABLE IF NOT EXISTS drafts (
  id           TEXT PRIMARY KEY,
  record_id    TEXT REFERENCES records(id),
  kind         TEXT NOT NULL CHECK (kind IN ('note','experience')),
  content_md   TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','confirmed','discarded')),  -- 人工确认前永远是草稿(宪法第 8 条)
  file_path    TEXT,                        -- 确认归档后的 Markdown 路径
  created_at   TEXT NOT NULL,
  confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS experience_index (
  id         TEXT PRIMARY KEY,
  title      TEXT NOT NULL,
  domain     TEXT NOT NULL,
  file_path  TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active')),
  source     TEXT NOT NULL CHECK (source IN ('voice','doc')),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          TEXT NOT NULL,
  device_id   TEXT,
  record_id   TEXT,
  intent      TEXT,
  risk_level  TEXT CHECK (risk_level IN ('L0','L1','L2') OR risk_level IS NULL),
  tool        TEXT,
  params_json TEXT,                          -- 转写文本只存截断/哈希(规约 §8)
  decision    TEXT NOT NULL CHECK (decision IN
                ('executed','confirmed','cancelled','timeout','failed')),
  result      TEXT,
  extra_json  TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_record ON audit_log(record_id);
