# 项目进展记录(供恢复会话使用)

> 最新状态:**A0 阶段全部完成,等待 Owner 做 Gate 0 验收(真机体验 + 协议冻结)**。
> 下次恢复时对 AI 说:"读 PROGRESS.md 和 docs/ 规约,我们继续",即可无缝接续。

## 2026-07-19(第一天)

### 今日完成

**需求与规约(docs/,编号即阅读顺序)**
- `01-需求文档.md`:总体需求(硬件+软件)。关键决策:逻辑上只有一个 Agent 在 PC/内网,工牌是端侧运行时;红线——录音必须按键触发(无唤醒)、屏幕只显示待办/定时提醒(不做通知中心)、音频转写后即删
- `02-Agent需求文档.md`:Agent 软件需求,13 条 FR 带验收标准,三档范围,演示方案(虚拟工牌 PWA)
- `03-项目宪法.md`:十条不可逾越原则,修改需 Owner 批准
- `04-开发规约.md`:工作流/协议治理/依赖批准/测试数据合规(三层黄金集)/代码规范/Mermaid 写作规则
- `05-规约对抗审查报告.md`:首次审查修正 5 处矛盾(含 14 周→4~6 周排期)
- `06-开发计划.md`:权威计划,两人团队,A0~A3,每阶段任务卡+Gate 验收
- `07-架构总览.md`:汇报版(Mermaid 图,可直接截图进 PPT)
- `08-架构设计.md`:状态机×3、模块划分、9 张数据表、目录结构
- `prompts/AI协作开发提示词.md`:跨项目复用的提示词库(规约驱动开发方法)

**代码与数据(A0 产出)**
- `backend/`:Python 3.12(uv 安装),FastAPI 骨架 + 适配层五接口 + Mock;`schema.sql` 9 张表
- `frontend/`:Vue3+Vite+TS+vite-plugin-pwa;protocol 类型与登记册对齐;四视图 + 状态机
- `docs/protocol.md`:协议登记册 v1.0(待 Owner 评审冻结)
- `testdata/`:L1 合成 160 条×3 档噪声(带标准答案)、L3 AISHELL 100 条、L2 公开音频 5 段;`benchmark/asr_report.md`
- 原型跑通:按住说话→真 ASR→关键词路由→任务执行/笔记草稿;"说完即消";音频即删;审计留痕。冒烟 12/12,pytest 19 绿 + 7 slow 跳过
- LLM Provider 骨架:默认 mock;OpenAICompatibleProvider 预留;Key 只从环境变量 `LLM_API_KEY` 读,缺失显式报错;`allow_external` 闸门;`.env` 已 gitignore

### 关键实测结论(ASR)

- 选型:**faster-whisper small + 固定简体 initial_prompt**,L1 近讲 CER 6.7%,RTF≈0.52
- FR-03(可用率 ≥90%)临界:L1 89.4%,L3 62%;补足路径:ITN、任务名 hotwords、前端降噪(见 benchmark 报告 §4)

### Gate 0 待办(Owner,明天第一件事)

```bash
cd /Users/datou/项目/office_agent
cp backend/config.example.yaml config.yaml   # 若已存在跳过
backend/.venv/bin/agent-host mock import
backend/.venv/bin/agent-host serve
# 电脑浏览器打开 http://localhost:8000/,按住"按住说话"试 5 句:
# 1.把周报撰写标记为已完成(卡片应即时消失) 2.新建一个任务明天之前回复客户邮件
# 3.查一下还有哪些没完成的任务 4.把周报标记为已完成(应出候选) 5.任意一句陈述(出笔记草稿)
```

- 体验 ≥5 次,确认交互手感(按键/红条/出结果速度/草稿结构)
- 评审 `docs/protocol.md`,无异议即冻结 v1.0
- 通过后进入 A1(06 计划 §3:正式配对、确认回路完整实现、无头测试客户端、弱网加固)

### 已知问题/注意

- 手机浏览器访问 `http://IP` 非安全上下文会拒麦克风;真机体验方案:`adb reverse` 端口转发(待做,已答应 Owner)
- `dev_mode: auto_approve` 与 `config.yaml` 仅限原型,严禁带出
- 本机系统 python3 是 3.9,venv 用 uv 装的 3.12;路径 `backend/.venv/`
- httpx 为测试期依赖(Owner 已批准);starlette TestClient 弃用警告留观察
- ASR 同音字靠归一化表 + difflib 兜底,参数未调优

### 协作模式备忘

- 一切编码从任务卡开始(06 计划);行为变更先改文档;红线在 03 宪法
- 新依赖需 Owner 批准(04 §4);测试命名带 FR 号;Mermaid 改后必须本地渲染验证
