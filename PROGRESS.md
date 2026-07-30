# 项目进展记录(供恢复会话使用)

> 最新状态:**A1-2 基线 3a922cb、校准基线 c91f437、A1-3 基线 b85ac00 与文档收尾 ad8df38 均已推送 origin/main;A1-4 第一小步"现场记录草稿人工确认归档"(FR-05 部分)已通过 Codex 复审,checkpoint 已建立并推送;FR-05 缺口"待办转任务草稿"仍未实现,列为 Owner 决策点**。
> 说明:未经 Owner 另行明确安排,不运行真实外部模型测试,不修改 LLM 接入代码;不索取、不接收、不展示任何 API Key。
> 产品决策(2026-07-22,最新):**方案 A 单屏 AI 工牌 + 三枚物理键**(主操作键[录音/麦克风标识]/上翻/下翻;第四枚"确认·返回"已删除不得恢复);方案 B 仅备选。
> 文档权威:`docs/v2.0/` 为**候选基线(评审中,暂不生效)**;`docs/` 旧文件仅历史参考(见 `docs/README.md`);进展/待审核/未提交状态只记在本文件。
> 下次恢复时对 AI 说:"读 PROGRESS.md 和 docs/ 规约,我们继续",即可无缝接续。

## 2026-07-29(A1-4 第一小步,复审通过):现场记录草稿人工确认归档(FR-05 部分)

按经 Codex 有条件通过的最小任务卡实施(不得称为 FR-05 收尾或 A1-4 完成):

- **范围**:只补笔记草稿人工确认 + 本机 Markdown 归档 + drafts 状态/路径 + 一条审计 + 工作台确认按钮 + 文档与定向测试;待办转任务草稿、experience 入库、discard 入口、编辑/搜索/版本/批量、schema 与端云协议变更、真实 LLM、新依赖全部排除。
- **实现**(生产代码净增 ≈95 行):
  - `DraftRepo.confirm(draft_id, file_path) -> bool`:UPDATE 带 `status='pending'` 条件,按受影响行数判定;重复确认返回 False,不产生第二次成功(原 `set_status` 桩替换;discard 入口不开放);
  - `LocalNotesAdapter`(adapters/notes.py):stdlib 写 `store.notes_dir`(新增配置项,默认 `data/notes`),文件名只用日期 + 完整 draft id,正文与 content_md 逐字一致;`NotesAdapter` 协议加 `draft_id` 参数,Mock 同步(同样按 draft_id 区分归档路径);
  - `FieldNoteSkill.archive`:只接受存在 + kind='note' + pending,否则 KeyError(404)/ValueError(409);notes 依赖注入,缺省 Mock 兼容存量装配;
  - `POST /desk/drafts/{id}/confirm`:成功只回 `{"status":"confirmed"}`(不回传 file_path);审计 device_id=None(不伪造 pc-desk)、record_id=草稿关联、intent=field_note、tool=notes.archive、risk_level=L1、decision=confirmed,不写转写/正文;
  - `/desk/drafts` 响应 +`id` 字段(Agent API,非端云协议);DeskView 仅 note pending 显示"确认归档",请求期防重复点击,成功/失败简短提示,不显示路径与内部细节。
- **文档**(同次):02 FR-05 范围说明改"归档已实现/待办转任务草稿未实现"、两处"同源只读"旧文字修正、Demo 注记缺口改"生成任务草稿";07 模块表 field_note/api 行;README §5 第 1 项收窄为待办转任务草稿缺口。
- **测试**:`test_fr05_archive.py` 6 条(落盘一致/状态字段/出队/审计字段/重复 409 无第二审计与文件/experience 409/未知 404/repo 行数守卫,全 tmp_path 隔离);desk.spec.ts 改 1 增 2(确认成功消失不显示路径/失败简短提示;隔离库种子,不碰根 agent.db)。

验证:见本轮汇报(定向 FR-05 / ruff / `--runslow` 全量 / typecheck·lint·build / desk.spec.ts / `git diff --check`);真实 LLM Gate 未运行。

**复审修复(同日)**:文件名"日期 + id 前 8 位"碰撞导致静默覆盖(实证:deadbeef-11111111/-22222222 同文件)。仅改 `adapters/notes.py` 与 `test_fr05_archive.py`:文件名改"日期 + 完整 draft_id",Mock 同步按 draft_id 区分;不引入随机数/计数器/查重/版本/文件锁;API/Skill/Repo/前端/配置/schema/协议不动。回归 2 条:Local 同前缀双 id 双文件正文分别保留;Mock 同 title 双 id 两条记录。定向 8 passed、ruff 绿、全量 `--runslow` 132 passed、1 skipped、`git diff --check` 干净。

## 2026-07-22(A1-3 极小修复):confidence 超大整数 / entities 严格类型

复审边界两修(生产代码仅 router/router.py,净增 ≈5 行):①校验顺序调整——先比 0~1 界(超大整数比较不抛 OverflowError)再查有限值,非法一律回退规则;②entities 字段缺失可用空字典,存在时必须 dict([]/""/0/null 均非法回退)。回归:`test_fr04_llm_huge_confidence_no_exception_no_stuck`(10**1000 不炸、records 到 done)、`test_fr04_llm_entities_list_rejected_rules_extract`(entities=[] 回退规则并正确抽取任务标题)。文档:删 PROGRESS 顶部过期"暂停 A1-3"行、回归文件说明 135→150。验证:ruff 绿;`pytest --runslow` **124 passed、1 skipped(真实 LLM Gate 仍未配置,如实 skipped)**;`git diff --check` 干净;未提交、未推送。

## 2026-07-22(A1-3 复审小修):运行时暂停第三方 LLM / 输出校验 / 语料补足

A1-3 复审未过,按硬约束小修(生产代码净增 ≈30 行):

1. **运行时暂停第三方 LLM**(宪法第 3 条:人名/组织名脱敏与出域审计未就绪):`create_app` 对非 mock provider 显式抛 `LLMNotConfiguredError`,运行时恒为纯规则路由;`OpenAICompatibleProvider` 仅供合成黄金集验收测试直接调用;未加新配置项/新 provider。测试 `test_runtime_rejects_third_party_llm`。
2. **LLM 输出校验补齐**(`_classify_llm` 内联,无 Schema 库):confidence 必须非 bool 数值、有限、0~1;complete_task.task_title / create_task.task_title/due / create_reminder.remind_query 必须 str|None;非法即回退规则。回归:`test_fr04_llm_confidence_nan_inf_out_of_range_rejected`(NaN/inf/-0.1/1.1/bool/str 全回退)、`test_fr04_llm_bad_entity_type_falls_back_without_stuck`(task_title 数组不炸,process_text 到 done 不卡 routed)。
3. **语料补足**:L1 labels/texts 各 +15 条(FIELD/TASK/EXP-046~050),三类达各 50 条、共 175 条;新条目仅文本无音频,`bench_asr.py` 加 2 行守卫自动跳过;`test_fr04_l1_rules_fallback_regression` 计数动态化并断言语料规模(50/50/50/25)。
4. **真实 LLM Gate 未运行**:环境无 LLM_API_KEY/LLM_BASE_URL/LLM_MODEL,`test_fr04_l1_llm_acceptance` 如实 skipped,不宣布 A1-3 通过。
5. 文档:v2.0/02 FR-04 与 07 router 行改"运行时暂停"、v2.0/README 未决清单 +1(第 10 项:脱敏+出域审计安全任务)。

验证:ruff 绿;`pytest --runslow` **122 passed、1 skipped**;`git diff --check` 干净;未提交、未推送。

## 2026-07-22(A1-3):意图路由 LLM 分类 + 规则兜底(FR-04)

- **校准基线已提交**:c91f437(本地,未 push);A1-3 开工。
- **勘察量化**:规则兜底在 L1 黄金集基线 57.8%,且安全零容忍违例 2 例(FIELD-012"运维同学提醒…"被"提醒"误命中、FIELD-042"…已经完成了…"被"已完成"误命中)——证明 LLM 分类的必要性与规则收紧点。
- **实现**(router/router.py,核心层零 Web 依赖不变):
  - auto 判定顺序:显式模式优先 → LLM 分类(四类 + 白名单指令 + 参数,JSON 结构化;`_build_llm_prompt`)→ 关键词规则兜底;
  - LLM 置信度 < 0.6 → unknown(反问,不走规则);LLM 异常/非法输出(intent 越界/指令非白名单/entities 非对象)→ 规则兜底;task_command 无指令名用规则补齐,补不出 → unknown(不猜测执行);
  - 规则收紧:完成指令只认"标记为已完成/标记完成/标为完成/设为完成";提醒指令去掉裸"提醒"(保留 提醒我/定时提醒/定时任务)——L1 上 field/experience → task_command 归零;
  - app 装配:mock provider 传 `IntentRouter(None)`(规则即 Mock 语义),真实 provider 启用 LLM 分类(双闸门不变)。
- **测试**:`test_fr04_llm_router.py` 10 条桩 LLM 用例(采用/低置信反问/异常兜底/非法兜底/白名单外兜底/规则补齐/补不出 unknown/参数透传/remind_query 兜底/显式模式不调 LLM);`test_fr04_l1_regression.py`:规则回归(零容忍 = 0 硬断言 + 准确率地板 0.58 + field 召回 ≥0.9,混淆矩阵透明输出)与 **`test_fr04_l1_llm_acceptance`(Gate 1 实测:`--runslow` + `LLM_ACCEPTANCE=1` + `LLM_API_KEY/LLM_BASE_URL/LLM_MODEL`,断言 ≥95% 与零容忍,CI 默认跳过)**。
- **文档**:v2.0/02 FR-04(LLM 分类语义/兜底/反问/白名单/验收口径)、07 router 模块行同步。
- 验证:后端 ruff 绿、`pytest --runslow` **119 passed、1 skipped(验收实测,待真实 LLM)**;前端未改动,未跑 e2e。
- **如实结论**:规则兜底单独达不到 Gate 1 指标(59.3%),它是可用性兜底;≥95% 指标取决于真实 LLM 验收实测(待 Owner 配置 provider 后运行)。

## 2026-07-22(小修轮):旧库兼容 / clarify.select 校验 / revoke 清理 / e2e 假绿

小修(非架构升级;生产代码净增 ≈70 行):

1. **旧数据库兼容**:`store/db.py` 增加 `_upgrade_records_if_needed`(幂等)——PRAGMA 探 records 缺 source 或 device_id NOT NULL 才重建复制,旧记录 source 默认 device_audio,临时关外键(drafts 引用);无迁移框架。测试 `test_fr04_legacy_records_upgrade`(旧表一条数据,升级后数据在、可写 pc_text、二次 init 幂等)。
2. **clarify.select 最小安全校验**(app.py `_on_clarify_select`,用 records + result_cache):record_id 存在且归属当前 device、缓存确为 clarify、candidate_id 是下发候选或类型匹配的 task:/remind:cancel;不满足直接忽略(不改库不审计)。测试 `test_fr08_clarify_select_minimal_validation`(他人设备/乱序 id/类型不匹配/不存在/终态后重发 5 类忽略 + 合法取消对照)。
3. **revoke 清理**:manager.revoke 补一行清 capabilities;测试 `test_fr01_revoke_clears_capabilities`。
4. **文档**:v2.0/01 确认分支改"长按主操作键确认、短按取消"(只动冲突文字)。
5. **e2e 假绿修复**:badge-keys"结束并上传"用例改路由受理 + 显式断言(一次 POST、非空 X-Device-Id、200),测试日志不再出现该用例导致的 500(两轮全日志 grep 为 0)。

验证:后端 ruff 绿、`pytest --runslow` **108 passed**(+3);前端 typecheck/lint/build 绿;Playwright 两轮 **31/31、31/31**;`git diff --check` 干净。

## 2026-07-22(最终修正轮):三键 / 布局 / 会话唯一 / capabilities 生命周期 / clarify 取消 / 来源中立

Owner 终审 6 项修正,已全部落实(改动仍未提交、未 push;保留 A1-2 checkpoint 3a922cb):

1. **三枚物理键(Owner 最终决定)**:主操作键 action(实体录音/麦克风标识)/上翻/下翻;第四枚"确认·返回"已删除不得恢复。action 按状态复用:普通页短按开始录音、录音中短按结束上传、clarify 短按选定、clarify 长按取消、普通页长按回身份首页、失败结果页短按关闭、L2 确认页**长按确认/短按取消**(与旧四键相反);双击只预留不实现,单击零延迟。`DeviceKey = action|page_up|page_down`,capabilities.keys、PWA、协议、文档、测试全改三键;画布内仍零触摸按钮。
2. **布局规则**:状态栏顶/提示底/内容区中;身份首页信息组水平+垂直居中(文字二维码居中);待办/提醒/简报/候选/确认/结果页内容组垂直居中、文字左对齐;A/B 同规不写绝对坐标;e2e 相对盒断言(±8px + textAlign)。截图已全部更新至 docs/v2.0/assets。
3. **设备会话唯一**:hello 认证成功即退休同 device_id 旧连接(服务端关闭);`on_message` 活动性守卫——旧连接的 record.start/clarify.select/confirm.response/state.sync.request 整条忽略;finally 身份守卫保留(旧连接迟到退出不删新连接)。
4. **capabilities 绑定当前连接**:每次 hello 必替换快照(未携带→清除);push/broadcast 发现当前连接失效时同步清理;旧连接退出不清新连接的值;test_fr01_reconnect 翻转重写(退休/替换/清除/身份守卫/旧连接消息无效果)。
5. **clarify 取消 = 完整服务端终态**:`task:cancel`(歧义与预览通用,新增 `TaskCommandSkill.cancel_pending`)/`remind:cancel`——不执行候选、records done、audit cancelled(意图取路由登记)、终态 intent.result("已取消",success)入缓存:凭据出队、duplicate 恢复重放终态不卡候选页。新测试 `test_fr08_clarify_cancel.py`(3 单元 + 1 端到端 duplicate)。
6. **来源中立 core**:records 表 device_id 可空 + 新增 source 列(device_audio/pc_audio/audio_file/pc_text,schema.sql);`process_text(text,confidence,mode,source,record_id?,device_id?)`——record_id 缺省真实登记(不伪造设备/录音记录),audit device_id 可 NULL;`ProcessOutcome` 只含 record_id + 消息序列,投递目标由 app 装配层决定。新测试 `test_fr04_pc_text.py`(无设备/无录音/不经 HTTP-WS)。

验证:后端 ruff 绿、`pytest --runslow` **105 passed**;前端 typecheck/lint/build 绿;`badge-keys.spec.ts` 重写 12 条(身份居中/垂直居中左对齐/三键+capabilities/action 状态映射短长按/L2 短按取消长按确认/候选双向/翻页循环);e2e 排障:测试服务吃 `dist` 构建产物(e2e 前必须 `npm run build`),confirm.request 仅 uploading/processing 可迁移 confirm_wait。

## 2026-07-22(校准修正轮):方案 A / 四键 / 后端韧性 / 文档候选基线

Owner 审核校准后给出 6 项修正,已全部落实(改动仍未提交、未 push):

1. **方案 A 统一(取代 07-22 早些时候的方案 B 决策)**:单屏 AI 工牌;已连接默认身份首页(姓名/部门/二维码,不含配对码);页序身份→待办/提醒→简报;上翻/下翻双向循环;方案 B 仅备选。PWA 页模型 [identity,...cards,briefing],`BadgeIdentity.vue` 共享身份片段;B 档仅作屏幕档位仿真。
2. **四枚物理键**:录音/上翻/下翻/确认·返回;确认·返回短按=确认/选择、长按(≥600ms,pointer 计时)=取消/返回/回身份首页,其余键长按 no-op;澄清候选上/下双向循环移动;画布内保持纯显示无触摸按钮。三层区分(物理键/手势/语义动作)集中在 `device-input.ts`(`DeviceKey` 改 record|page_up|page_down|confirm_back,`pressKey(key, gesture)`)。
3. **后端重连与发送韧性**:`handle_connection` finally 加身份守卫——旧连接迟到退出不清同 device_id 新连接与 capabilities(不主动关闭被替换连接,行为最小变更);`push`/`broadcast` 捕获 TransportClosed 按离线跳过(失效登记连接顺手移除)。确定性测试 `test_fr01_reconnect.py`(旧连接迟到退出)、`test_fr02_send_disconnect.py`(MockASR 闩锁,发送时断线仍终态落库/缓存/审计,重连 duplicate 补推恢复)。
4. **core 文本入口**:音频接收/转写/清理与统一投递 `_deliver` 移到 `api/app.py` 装配层;`core/processing.py` 零推送,提供 `process_text()/record_asr_failure()/on_clarify()` 返回 `ProcessOutcome`,终态落库/结果缓存/审计先于投递完成;设备录音/PC 文字/PC 录音/音频文件共用 process_text(`test_fr04_process_text.py` 直接调文本入口验证)。消息类型/payload/顺序、records 状态、审计字段不变。
5. **hello capabilities**:PWA hello 上报与方案 A 一致(audio webm-opus/1ch、screen eink 300×400+profile、keys 四元组、led/haptics/wifi);e2e `badge-keys.spec.ts` 7 条(身份首页/双向翻页/短按选定/长按回首页/长按失败返回/候选双向/capabilities 断言)。
6. **文档修正**:v2.0 全部 10 份头部改"候选基线(未经评审,暂不生效)/生效日期待定";方案 A/四键三层/capabilities 示例/韧性条款/process_text 边界写入 01/02/05/07/08/09;截图内聚 `docs/v2.0/assets/`(11 张,链接相对路径),`docs/assets/` 恢复为已提交历史状态;进展/待审核/未提交语句全部剥离(02 实现状态行、05 进展快照等已删),进展只记本文件;`docs/README.md` 改"候选基线(评审中)",不再称"唯一有效"。

验证:后端 ruff 绿、`pytest --runslow` **98 passed**;前端 typecheck/lint/build 绿;Playwright 连续两轮 **26/26、26/26**;`git diff --check` 干净;根 `data/agent.db` 未被 e2e 触碰。

## 2026-07-22(续):A1-2 基线 + 软硬件解耦校准

### A1-2 checkpoint

- Owner 验收通过;全部 A1-2 改动提交本地 checkpoint **`3a922cb`**(`feat(a1-2): 音频管线与置信度低路径——离线补传/错误分级/终态出队/duplicate 恢复(FR-02/03)`,15 文件,+1010/-21),**未 push**。

### 软硬件解耦校准(本轮改动未提交,待 Owner 与 Codex 审核)

- **后端边界(最小调整,行为不变)**:核心层对 Web 框架零依赖由 `tests/test_arch_boundaries.py` 守卫(ast 扫描);核心流程(转写文本→置信度闸门→路由→技能分发→结果→审计)从 `api/app.py` 抽入 `core/processing.py`(ProcessingDeps 依赖注入,app.py 仅装配)——设备录音/未来 PC 文字/PC 录音/音频文件共用同一核心流程;`gateway/transport.py` 新增 Transport 抽象(accept/receive_text/send_text/close,断开统一 `TransportClosed`),`WebSocketTransport` 为 FastAPI 唯一包装点,`gateway/manager.py` 不再 import fastapi;不加 MQTT、不实现 BLE。
- **Device Capabilities**:`device.hello` payload 新增**可选** `capabilities` 字段(audio/screen/keys/led/haptics/storage_mb/battery/network/firmware_version),`gateway/capabilities.py` 容错解析(未知字段忽略、非法输入全空),主机**只登记不消费不落库**(会话内存,断连清理);`display_profile` 保留,仍是渲染档位唯一权威;技能不判断分辨率/设备型号(原本即无,现由边界测试固化)。
- **PWA 硬件模拟修正**:电子纸画布(屏幕显示区)只显示内容——卡片/简报/录音状态/候选/确认/结果;画布内不再有"开始录音/点击结束"触摸按钮;画布外新增 `.hw-keys` 硬件控制区(录音/确认/返回/翻页四枚虚拟物理键,按状态禁用)。统一设备输入接口 `src/lib/device-input.ts`:`pressKey(record|confirm|back|page)` 唯一语义入口,虚拟物理键与手机演示按钮同走此接口,未来 ESP32 GPIO 映射同一语义;状态机与业务不区分事件来源。录音键语义不变:idle 按下开始、recording 再按停止上传、仅人工触发。
- **docs/v2.0 文档基线**:11 个新文件(README+01~09+docs/README.md),每份带统一头(文档集版本 v2.0/待评审基线/生效日期/权威范围);只留最终结论,删除修订记录与对抗审查过程(结论融入正文);10 项未决问题集中于 `docs/v2.0/README.md` §5;开发计划去日历化,A3 改"硬件接入准备(按需)";Mermaid 逐字复用旧版已验证图块(本机无渲染器)。`docs/README.md` 声明 v2.0 唯一有效、旧文档仅历史参考、保留不删。
- **测试**:后端新增 `test_arch_boundaries.py`(3 条)+ `test_fr01_device_capabilities.py`(4 条),全量 **90 passed**、ruff 绿;e2e 更新 `eink-shots.spec.ts`(物理键操作+画布无触摸按钮断言+截图改 .eink-frame)与 `recording.spec.ts`(新增 eink 物理键用例),其余 spec 未动,全量两轮 **19/19**;截图 docs/assets/eink-*.png ×10 已更新;typecheck/lint/build 绿;`git diff --check` 干净。

### 校准限制(本轮明确不做)

不开发 A1-3;不制定四个月计划;不实现 BLE/USB/MQTT/OTA/NFC/量产;不拆仓库;不重写 A1-2;不引入无关抽象;不 push;不自行宣布校准通过——待 Owner 与 Codex 审核。

## 2026-07-22(A1 开工):产品方向与 A1-2 离线补传

### 产品方向(Owner 决策,2026-07-22)

**方案 B 确认**:正面实体工卡展示身份(姓名/部门/二维码),背面电子纸只显示动态内容(提醒/简报/结果)。后续涉及身份页/显示契约的改动以此为准(A 档"无卡身份页"将在后续任务卡重评,不在 A1-2 范围)。

### A1-2 音频管线与置信度低路径(完成,待验收)

- **离线缓存自动补传(FR-02)**:上传网络失败或 5xx → 音频入端侧 IndexedDB 队列(`lib/pending-audio.ts`,record_id 幂等,服务端 duplicate 去重)→ "已离线缓存"提示 → `forceReconnect` 主动断开假死连接 → 退避重连 → hello 认证 + state.sync → `onRestored` 钩子自动补传 → 结果仍走 intent.result。
- **错误分级与恢复(2026-07-22 补)**:
  - 一切上传先入 IndexedDB 恢复凭据(record_id 幂等),**仅终态 intent.result(success/failed/low_confidence)到达才出队**;受理(200)后页面关闭/断连,重开经 duplicate 补推恢复;clarify/pending_confirm 中间态凭据保留,放弃后亦可经补推回到同一中间态;
  - 5xx/网络错误:自动退避重试(2s/5s/10s,用尽则保留队列等下次重连);4xx 永久拒绝:即时移除并提示;
  - 补推前归属校验(跨设备不可读);缓存淘汰/服务重启按 records 终态合成通用结果("已处理完成,请到电脑端查看详情");
  - 60s 弱网验收负载与 ASR 解耦(不可解码负载快进失败);门控 e2e 改假 WS 主机,消除真实 ASR 时序依赖,连续两轮全量稳定。
- **P1 时序修复(2026-07-22,验收发现)**:WS intent.result 先于 HTTP 200 到达时(如 duplicate 补推),状态机 uploading 无 IntentResult 迁移——事件被忽略、凭据已删、UploadDone 把状态推进 processing 卡死。修复:状态机补 uploading → IntentResult → Showing / ConfirmRequest → ConfirmWait 两条迁移,两种到达顺序收敛同一终态;晚到 UploadDone 在 Showing/ConfirmWait 无迁移不得覆盖。e2e `result-ordering.spec.ts` 确定性回归(WS 先送、≥500ms 后回 200):终态先到结果页在 200 前可见不停 processing、clarify 先到候选可见且凭据保留至最终终态;连续两轮 18/18。
- **置信度阈值配置化(FR-03)**:`asr.low_confidence_threshold`(默认 0.5)。
- **验收实测**:60 秒静音 WAV(≈1.87MB)在 100KB/s 限速下补传成功(19.4s,FR-02 口径);503 两次后自动退避重试成功;duplicate 补推全链路通过。
- 测试:后端 `test_fr02_offline_retry.py`(duplicate 补推);e2e `offline-audio.spec.ts` 3 条(断网补传/503 退避/限速验收);全量后端 71 passed、e2e 13/13。
- 假死连接场景:Playwright setOffline 等环境断网时 WS 不自动关闭,`forceReconnect` 主动断开以触发重连(真实离线同样更及时)。

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
