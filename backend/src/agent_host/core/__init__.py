"""核心编排层:文本处理管线(置信度闸门 → 路由 → 执行/草稿 → 出站消息;08 §1.2)。

零 Web 框架依赖(由 tests/test_arch_boundaries.py 守卫);外部依赖经 ProcessingDeps 注入;
core 不推送、不碰音频:产出 ProcessOutcome 由装配根(api/app.py)统一编排与投递。
"""
