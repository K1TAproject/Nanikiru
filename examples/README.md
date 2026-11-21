# 示例

当前无界面示例：从项目根运行 `.\.venv\Scripts\python.exe -m examples.inspect_position`。它构造新观察快照，计算指定弃牌后的牌形；不验证动作许可、不生成完整建议。

基础分析示例：`.\.venv\Scripts\python.exe -m examples.analyze_basics`，展示向听、等待、指定弃牌的 Metric/CandidateResult，以及明确条件下的番符支付。接口和结果边界见[分析模块说明](../docs/analysis-module.md)。

`completed_round.json`、`paused_round.json` 是旧 Recorder 样例；`exhaustive-game.json`、`settled-game.json` 是旧 Game 样例。原数据保留，不属于 observation-v1，本轮没有转换或加载它们。旧执行脚本和加载器已移除；不要把这些文件直接作为新分析请求。

统一调用链示例：`.\.venv\Scripts\python.exe -m examples.analysis_pipeline`，从人工记录分别提取初始/弃牌后的请求，经 analyze 返回统一结果摘要，展示历史前缀与非弃牌阶段的处理。见[分析调用链](../docs/analysis-pipeline.md)。

连续单局无界面示例：`python -m examples.simulate_round`，固定种子开局，经可见记录做一次分析，再以合法简单动作推进至结算。
