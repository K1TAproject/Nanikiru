# 人工记录到统一分析输出

更新：2026-11-11。本阶段已实现同步调用链，不新增牌局状态类，不修改原单局面 `test_ui/`。

```text
RoundRecord + 修订版本 + 回放步数
  → processing.analysis_input.extract_player_view
  → processing.analysis_input.build_analysis_request → AnalysisRequest
  → analysis.analyze → AnalysisResult
  → analysis.output → event_ui 同窗口摘要和实际输入/输出 JSON
```

## 输入提取与版本

```python
extract_player_view(record, through=None, *, observer_seat=None)
# 返回独立的 (position, history, coverage)
build_analysis_request(record, *, source_version, through=None,
                       observer_seat=None,
                       tasks=("shanten", "tenpai", "discard_shape", "recommend"),
                       method="minimal-shape-v1", objective="shape", method_options=None)
# 返回 AnalysisRequest
```

`through` 是已应用的事件数量，不是事件 ID；0 表示接入快照，省略表示最新。只重建该前缀，不读取后续事件载荷。`state_version` 为 `source_version:事件数量`，同时标识记录修订与所选时点。Session 在推进、撤销和加载后改变修订版本，避免撤销重录后复用旧结果。函数不修改原记录。

本提取器面向已经按玩家视角整理的人工/模拟/牌谱记录，只支持记录的原观察者，不可凭记录切换到另一家的暗手视角。保留明确 UNKNOWN、completeness 与 coverage；不补造中途接入之前的历史。

当前入口仅允许 `initial_context.riichi`，作为保留历史开始前四家的已确认立直状态，其他键拒绝；draw_context、groups、completeness 只保留已定义键，pending.responses 只保留观察者自己的响应。人工事件载荷由现有 processing 校验。这不是任意全知模拟器/牌谱的隐私净化器；后续模拟器、牌谱和视觉输入仍需各自的可见事实适配。

## 调度接口

`nanikiru.analysis.analyze(request) -> AnalysisResult`，方法由新增的 `AnalysisRequest.method` 选择，默认 `minimal-shape-v1`。目前仅支持 `objective="shape"`。

| 方法 | 支持任务 | 返回内容 |
| --- | --- | --- |
| basic-v1 | shanten、tenpai、discard_shape | situation.shanten、situation.waits、弃牌候选指标 |
| minimal-shape-v1 | 上述任务及 recommend | 另返回 situation.discard_ranking 条件牌效偏好 |
| fixed-v2 | 上述任务及 recommend | 人工参数综合/进攻/防守排序、条件打点和危险指数；见 [v2](fixed-v2.md) |

仅计算请求任务及其依赖；recommend 必须先计算候选牌效，因此也返回 candidates 与 situation.discard_shape。discard_shape 指标表示候选计算状态及数量，具体向听、改善牌和未见进张数在各候选 metrics 中。未请求的指标不填零或假结果。

未知方法、任务、目标或非法请求抛出 ValueError，不自动换算法。完整期望牌效、风险概率、神经网络方法未实现；fixed-v2 已在 recommend 的候选评价中调用条件打点，method_options 只接受 v2 的已定义权重。

只有本人待弃牌阶段才计算弃牌比较。其他明确阶段返回 not_applicable；阶段或行动方未知返回 unavailable。自家牌形不全不能给确定向听；公开覆盖不全仍能算向听和理论改善牌，进张数量不可用，排序降为仅比较向听。

人工记录尚无完整资格历史，候选 legality 保持 unknown；牌效排序为 conditional/heuristic，放在 situation.discard_ranking，人工记录的 `recommendations={}`。这可以显示“条件牌效偏好”，不能作为已验证的执行指令。进张数也不是和牌概率。

新增[模拟器来源](simulator.md)及[天凤适配](tenhou-adapter.md)通过各自的可见事件进入同一请求链。完整模拟历史可确认普通/立直/食替弃牌许可，候选标 allowed/forbidden，recommendations 只引用 allowed。这不代表已实现吃碰杠策略比较或完整和牌许可分析任务。

## 统一展示与调试

`analysis/output.py` 的 `summarize_result(request, result, selected_tile_id=None)` 只格式化已有结果，不重新计算。`require_current_result` 检查版本与方法；`debug_json` 序列化实际请求/结果，UNKNOWN 显示为 `{"$unknown": true}`，None 显示为 null。该 JSON 是调试展示，不是新增存档格式或加载接口。

事件 UI 使用 `event_ui/analysis_panel.py`：

1. 默认自动分析；推进、撤销、回放、方法/任务变化时清除旧结果，并按当前设置重新请求。
2. 关闭“自动”后，按“分析当前时点”运行；推进后不保留过时建议。
3. 点击手牌仅选择已有候选结果，不再触发独立牌效计算。
4. 切换 basic-v1 时需取消“牌效排序”；不支持的组合会明确显示错误，不偷偷改任务。
5. 勾选 JSON 在同一窗口下方并排显示实际输入和输出，手牌、牌河和历史仍可见。错误以诊断对象展示，不冒充 AnalysisResult。

当前为同步执行，任务变化会清除结果；尚无异步后台任务或取消协议。界面新建记录仍使用 0 家视角。

## 验证与运行

```powershell
.\.venv\Scripts\python.exe -m examples.analysis_pipeline
.\.venv\Scripts\python.exe -m event_ui
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m event_ui.pipeline_smoke
```

本阶段新增 11 项单元测试，总数 69：覆盖前缀隔离、输入不变、未知降级、方法/任务选择、版本、输出复用及可见信息边界。桌面检查覆盖实际 JSON、自动/手动、旧结果拒绝、回放、控件布局；已查看紧凑窗口及展开 JSON 的中途接入窗口截图。测试不代表完整麻将规则或任意输入源适配已完成。
