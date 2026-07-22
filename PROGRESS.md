# 项目进展记录(供恢复会话使用)

> 最新状态:**Gate 0 阻断修复(两轮)已实施,待 Owner 按"Gate 0 待验收清单"验收;验收通过前,任何人(含 AI)不得声称 Gate 0 已通过**。
> 下次恢复时对 AI 说:"读 PROGRESS.md 和 docs/ 规约,我们继续",即可无缝接续。

## 2026-07-21(五):Gate 0 通过 + A1-1 正式配对落地

- **Git 基线**:`335e4ef` 已推 origin/main(Gate 0 验收通过;41 文件,无配置/数据/产物入库)。
- **A1-1 设备接入(FR-01)**:正式配对闭环——pair.request(6 位码,5 分钟一次性)挂起 → `agent-host pair approve <code>`(经本机 `POST /desk/pair/approve`,单一事实源)签发 device_id + token(devices 只存哈希)→ 端侧持久化后 hello 认证进入 idle;hello 正式模式校验 token 哈希(pair_required/auth_failed/revoked 三态);`agent-host pair revoke <device_id>` 吊销即时失效并向在线设备推送 device.revoke;音频上传正式模式校验 X-Token(dev_mode=auto_approve 为原型/测试旁路,正式部署必须关闭)。
- 前端:身份页展示配对码与等待提示;pair.result 批准后自动持久化并重连;revoked/auth_failed 清除身份回未配对页。
- 测试:`test_fr01_pairing.py` 8 条(hello 三态/完整配对/吊销即时失效/上传 token 校验/过期码/非法码/吊销推送),TestClient WebSocket 全流程;后端全量 `pytest --runslow` **78 passed**;前端 typecheck/lint/build 通过;Playwright **6/6**(隔离环境)。
- 限制:配对挂起状态在内存(进程重启后需重新发码);Owner 当前 config.yaml 仍为 auto_approve(正式配对体验需移除该节并重启);A1-1 验收以后端测试为准,正式配对的浏览器 e2e 未单列。

## 2026-07-21(四):验收阻断修复(typecheck/PWA 更新/e2e 隔离/多任务扩展)

1. **typecheck**:CardsView v-for 未用变量修复;`typecheck`/`lint`/`build` 均通过。
2. **PWA 更新失效**:`vite.config.ts` workbox 增加 `skipWaiting + clientsClaim + cleanupOutdatedCaches`(原仅 autoUpdate,新 SW 永远等待旧标签页关闭,浏览器停留旧 bundle);发布新构建后 SW 激活即接管并清旧预缓存。
3. **e2e 隔离**:`playwright.config.ts` 改为独立测试服务(`e2e/start-test-server.sh`:端口 8100 + `frontend/.e2e-runtime` 临时库,每次重建,`reuseExistingServer: false`);各 spec 的 `mock import` 改走 `e2e/seed.ts` 的 `reseed()`(作用于隔离库);不再触碰 8000 服务与根 `data/agent.db`(已验证:e2e 运行后根库 mtime 不变)。
4. **多任务扩展**:新增"X 得准备/X 也要弄"宾语前置与"这是 N 个任务"元尾注处理、"提前"子句切分;"会议论文得准备,说明文档也要弄一下"→ 两项预览;"提醒我明天九点半开会,提前准备…,然后整理…,这是两个任务"→ 复合预览(提醒与两任务分列),确认才全部写入,取消无写入。

验证:后端 `pytest --runslow` **70 passed**;e2e **6/6**(隔离环境);typecheck/lint/build 通过。

## 2026-07-21(三):PWA 待办体验修复(布局/勾选完成/多任务)

### 问题与修复

1. **排版**:普通 PWA 页原垂直居中、长文挤压。改为顶对齐响应式布局(`.phone` 类隔离,电子纸档样式完全不受影响);任务卡片式列表,长标题自然换行。
2. **待办结构化**:列表加 checkbox,触控勾选完成(任务卡)或取消(定时提醒卡);后端新增 `POST /desk/tasks/{id}/complete`(完成任务+撤卡+广播同步)与 `POST /desk/reminders/{id}/cancel`;协议 card 兼容新增 `ref_task_id`(修订6);工作台"待办任务 / 提醒"区加完成/取消按钮。
3. **多任务语义**:规则切分(另外/还有/然后/接着/同时/并且/以及/标点;"和"、"、"不切),识别为多条待办后一律经**可编辑预览确认**才创建;无显式创建动词但有"并列词+动作动词"的口语句也按多任务猜(同样必须确认);`clarify.select` 兼容新增 `edited_labels`(修订6),PWA 可编辑标题后回传;电子纸档以正文编号列表 + 确认/取消键呈现。
   - 例:"下午要准备会议论文,另外把 WorkBuddy 和 Codex 的使用说明整理一下。" → 预览 [准备会议论文 / 整理 WorkBuddy 和 Codex 的使用说明],确认后创建两条。

### 验证

- 后端 `pytest`:**57 passed**(+12 条:拆分规则/路由护栏/预览确认闭环/desk 接口;临时库);`ruff` 全绿。
- e2e `tasks.spec.ts`(新):顶对齐断言 + 勾选取消提醒/完成任务并核对 /desk/tasks 状态。
- 限制:切分为规则式(非 LLM),无并列线索的纯陈述仍走现场记录;预览上限 5 条(超出并入末条);多任务共享同一截止时间;"把字句"动词前置仅覆盖常见动词表;电子纸档不支持编辑(按键确认/取消)。

## 2026-07-21(续):ASR 热词 + 定时提醒闭环 + 工作台任务区

### 起因(Owner 真实录音反馈)

- 专有名词 `WorkBuddy` 被识别为"我把";"定时任务"被识别为"定书任务";
- "创建一个定时任务"被路由成普通任务(或无声失败),工牌/工作台/提醒卡都看不到结果。

### 本轮实现

- **ASR 热词/业务词表**:`asr.hotwords` 配置项;faster-whisper 1.2.1 原生 `hotwords` 参数直接传入。真实词表只在 Owner 本机 `config.yaml`(已加 WorkBuddy/定时任务/定时提醒,不入库);`config.example.yaml` 只给 `<业务系统名>` 匿名占位。热词仅改善识别,不改变路由/校验/确认语义。
- **定时提醒闭环**(产品定义:"定时任务" = 一次性、可取消的定时提醒,非周期 cron):
  - 路由新增白名单第 4 指令 `create_reminder`("提醒我/定时任务/定时提醒/提醒"触发;完成动词仍优先;"取消提醒"本轮明确拒绝);
  - 解析相对日期(今天/明天/后天)+ 时间(上午/下午/晚上 N 点(半/N 分)、HH:MM、N 分钟/小时后)→ 明确 ISO8601;
  - 明确→直建 timer 卡;缺日期/时间已过→clarify 候选确认(`remind:confirm`/`remind:cancel`),确认前不写入,取消无写入;缺时间/缺内容→失败提示,不猜测执行;
  - 成功文案如"已创建提醒:明天 10:00 给 WorkBuddy 发周报";审计区分 confirmed/cancelled;
  - **最小到点触发**:30s 扫描到期 timer 卡广播 `reminder.push`;触发记录仅内存,重启后过期未撤卡补触发一次(08 §2 已注明)。
- **工作台"待办任务 / 提醒"区**:`GET /desk/tasks`(tasks + timer 卡,标题/类型/时间/状态/创建时间);普通 create_task 成功后同样可见。
- **测试**:新增 `test_fr03_asr_hotwords.py`(配置加载/参数传递/真机无退化 slow)、`test_fr07_create_reminder.py`(路由护栏/7 类时间解析/直建/澄清/取消/过期/触发去重/两条端到端);全部临时数据库,禁止 mock import 污染。全量 `pytest --runslow` **53 passed**。

### 本轮限制(如实)

- 周期性重复提醒未支持;语音"取消提醒"明确拒绝(卡片仅随任务完成撤下);
- 触发记录仅内存(重启补触发一次);"下班前"等模糊时间 → 失败提示而非猜测;
- 触发词为固定集合,陈述句含"提醒/定时任务"会被路由到提醒(缺时间则失败提示);
- WorkBuddy 真机改善依赖本机 config.yaml 词表,合规语料无该词,以传递测试 + 本机体验共同验证。

## 2026-07-21(Gate 0 阻断修复)

### 阻断项(Owner 判定 Gate 0 不通过的原因)

1. 内部规则泄漏到用户界面:工牌结果页显示"请到 PC 确认归档(宪法第 8 条)";草稿正文含"Mock 草稿 / 宪法第 8 条 / record_id"。
2. 无可观察性:转写文本与待确认草稿写入本机 SQLite,但用户没有任何 PC 页面或入口查看;`FieldNoteSkill.archive()` 未实现,"确认归档"是错误承诺。
3. 录音测试失效:静态录音页已无 `.timer`,e2e 仍断言它;结果页约 3 秒即返回,失败提示一闪而过。

### 本轮修复(已实施)

- **用户文案去内部化**:工牌提示改为自然语言——"未能识别,请在安静处重新录音" / "没有听清,请重新录音" / 草稿成功时"请到电脑端查看待确认草稿";草稿正文只含四段结构化内容,不再含内部术语;工牌身份页"研发部(占位)"改"研发部"。
- **PC 草稿工作台**:`http://localhost:8000/?desk=1`(同源只读;不影响工牌入口 `/` 与 `?eink=a|b`)。展示最近处理记录(时间/处理状态/转写文本/识别置信度)与待确认草稿(类型/创建时间/正文/待确认),每 5 秒自动刷新。后端只读接口:`GET /desk/records`、`GET /desk/drafts`(不返回内部编号、错误码、token 或堆栈)。
- **结果页停留规则**(08 §6.1/§6.2 已同步):成功 ≤3s 自动返回原页面;失败/未听清停留,点击返回,不再一闪而过。
- **测试**:`frontend/e2e/recording.spec.ts` 改断言静态录音页契约(黑条/"录音中"/"点击结束",断言 `.timer` 不存在);新增 `frontend/e2e/desk.spec.ts`(工作台入口 + 界面无内部术语,只读);新增 `backend/tests/test_fr05_field_note_desk.py`(slow):FIELD-001 真 ASR → 路由 → 笔记草稿 → 工作台可见,运行时数据全部隔离在临时目录,不碰本机 `config.yaml` 与 `data/agent.db`。
- **文档**:03 宪法第 2 条改"单键切换录音"(v1.1,Owner 批准);06/07 去除"按住说话"残留;02 FR-05 补工作台查看入口。

### 关键事实(验收前必读)

- **音频转写后默认删除**(`data/audio_tmp/` 无残留,不可恢复);转写文本与待确认草稿在本机经草稿工作台查看。测试音频与真实音频生命周期一致。
- **归档功能未实现**(`FieldNoteSkill.archive()` 仍为 `NotImplementedError`):草稿只能查看,不能确认入库;界面没有也不得有归档按钮或承诺。归档属后续任务卡。
- **运行时数据库实际路径**:仓库根 `data/agent.db`(`agent-host serve` 从仓库根启动,`config.yaml` 的 `store.db_path` 为相对路径)。`backend/data/agent.db` 是 7-19 从 `backend/` 目录运行时的残留(该目录下找不到根 `config.yaml`,配置落到默认相对路径);当前服务不使用它,保留未删。
- 数据全部留在本机:语音、转写、草稿不上传任何外部服务(LLM 当前为 mock,未接外部 API)。

### Gate 0 待验收清单(Owner)

1. 手工验收(见下方命令):工牌端录音 → 看结果反馈 → 打开 `http://localhost:8000/?desk=1` → 看到对应转写文本与待确认草稿 → 确认 `data/audio_tmp/` 无原始音频残留。
2. 工牌与草稿工作台全界面无宪法/FR 编号/Mock/章节号等内部术语。
3. 以上确认后,评审并冻结 `docs/protocol.md`,Gate 0 才算通过。

```bash
cd /Users/datou/项目/office_agent
backend/.venv/bin/agent-host mock import   # 重置演示数据(可选)
backend/.venv/bin/agent-host serve
# 工牌端:浏览器打开 http://localhost:8000/
#   点击"开始录音"说一句话 → 点击"点击结束" → 看结果页(成功 3s 返回;失败/未听清停留,点击返回)
# 电脑端:打开 http://localhost:8000/?desk=1
#   在"最近处理记录"看到该次录音的转写文本与状态;在"待确认草稿"看到对应草稿(仅可查看)
# 检查:ls data/audio_tmp/ 应为空
```

## 2026-07-20(显示契约竖向化 + Git 基线)

- Owner 决策(已入 protocol.md 修订3/4、08 §6):工牌外形 60×90mm 竖向、模组与外形分离;页面模型改"默认提醒页(单卡)+ 手动简报页";录音页静态化(取消每秒计时);全部 UI 竖向排版。
- 电子纸仿真:A/B 画布统一 300×400 竖向、禁滚动;p-* 页面模型样式补齐;删除 b-* 死样式。e2e `eink-shots.spec.ts` 重写,10 张效果图存 `docs/assets/`,2/2 通过。
- Git 基线:`7256db4` on main,已推送 https://github.com/Eric0703/office_agent(Public;`config.yaml`、`agent.db`、L2/L3 音频、模型权重不入库)。

## 2026-07-19(第一天)

### 当日完成

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
- 原型跑通:单键切换录音→真 ASR→关键词路由→任务执行/笔记草稿;"说完即消";音频即删;审计留痕。冒烟 12/12,pytest 19 绿 + 7 slow 跳过
- LLM Provider 骨架:默认 mock;OpenAICompatibleProvider 预留;Key 只从环境变量 `LLM_API_KEY` 读,缺失显式报错;`allow_external` 闸门;`.env` 已 gitignore

### 关键实测结论(ASR)

- 选型:**faster-whisper small + 固定简体 initial_prompt**,L1 近讲 CER 6.7%,RTF≈0.52
- FR-03(可用率 ≥90%)临界:L1 89.4%,L3 62%;补足路径:ITN、任务名 hotwords、前端降噪(见 benchmark 报告 §4)

### 已知问题/注意

- 手机浏览器访问 `http://IP` 非安全上下文会拒麦克风;真机体验方案:`adb reverse` 端口转发(待做,已答应 Owner)
- `dev_mode: auto_approve` 与 `config.yaml` 仅限原型,严禁带出
- 本机系统 python3 是 3.9,venv 用 uv 装的 3.12;路径 `backend/.venv/`
- httpx 为测试期依赖(Owner 已批准);starlette TestClient 弃用警告留观察
- ASR 同音字靠归一化表 + difflib 兜底,参数未调优

### 协作模式备忘

- 一切编码从任务卡开始(06 计划);行为变更先改文档;红线在 03 宪法
- 新依赖需 Owner 批准(04 §4);测试命名带 FR 号;Mermaid 改后必须本地渲染验证
