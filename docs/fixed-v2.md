# 人工参数 v2

更新：2026-11-13。已实现 `fixed-v2`，入口仍为 `analysis.analyze(AnalysisRequest)`；不是训练模型，也不是完整期望收益搜索。原单局面 `test_ui/` 未修改。未听牌路线与动作候选见[动作扩展](action-v2.md)。

## 使用

运行 `python -m simulator_ui`，在分析方法中选 `fixed-v2`。同窗口的“v2 权重 JSON”可输入 `{"risk": 12, "value": 2}`，点击“应用”。未填写的权重使用默认值，`{}` 恢复默认；未知参数、负数、布尔值、非有限数字会报错，不回退算法。事件工具同样支持此入口；人工记录没有完整资格历史时只能给条件偏好。

```powershell
.\.venv\Scripts\python.exe -m examples.analyze_v2
.\.venv\Scripts\python.exe -m examples.analyze_v2 --parameters weights.json --json
```

`weights.json` 是同样的 JSON 对象。程序入口：

```python
request = build_analysis_request(record, source_version="revision-1",
    method="fixed-v2", method_options={"risk": 12, "value": 2})
result = analyze(request)
```

仅支持现有 `objective="shape"`，不支持名次目标。任务为 shanten、tenpai、discard_shape、recommend；v2 路线与动作扩展在请求 recommend 时计算。

## 评分

每张候选先计算弃牌后向听、未见进张，再加入以下人工权重：

| 参数 | 默认 | 加权项 |
| --- | ---: | --- |
| shanten | 4 | 减去向听数 × 权重 |
| ukeire | 2 | 未见进张 / 全部未见牌数 |
| lookahead | 3 | 假设摸进改善牌，选最小向听且最大进张的后续弃牌，比较下一步进张比例；成形记 1 |
| value | 1.5 | 已听牌时的条件自摸收款 / 8000 |
| bonus | 0.25 | 留在暗手中的赤宝、表宝枚数 |
| risk | 7 | 减去当前弃牌危险指数 × 动态风险权重 |
| reserve | 0.3 | 留在手中的、对三家均为现物的牌数 |
| late_defense | 0.8 | 风险权重乘以 `1 + late_defense × (70-剩余摸牌)/70`；剩余未知时不加速 |
| route | 2 | 未听牌路线潜力 |
| route_speed | 1 | 路线内相对最快牌形的距离损失代价 |
| route_ukeire | 1 | 可计算路线的未见进张奖励，未知时不添加 |
| commitment | 0.5 | 副露、杠和立直锁手代价代理，见动作扩展 |

lookahead 是**改善后的质量**，不是第二遍当前进张；以未见牌枚数加权，假设忽略他家打断。未见牌包含对手暗手和牌山，不能把比例宣称为实际摸进概率。进攻排序使用前五项；防守排序只按危险指数由低到高；综合排序加入风险和安全牌保留项。同分并列，所有排序排除 forbidden；只有确认 allowed 时才填可执行 recommendations。

条件打点沿用项目番符计算。只对当前听牌形，枚举未见和牌张，按当前已成立立直与规则计算自摸支付，以剩余枚数加权。不假设未来立直、不预测里宝、不计一发/海底/岭上等特殊和牌、不计本场供托。这是“成和条件下的打点”，不是期望净收益；未听牌时不伪造打点，宝牌保留只作为粗略潜力分。

危险指数使用他家牌河、立直成立后的已过牌、公开副露数、庄闲、字牌可见数、筋和幺九等信息。现物的 0 仅指对指定玩家当前荣和；筋和字牌减少风险分，但不保证安全。三家风险以最大值加其余值的四分之一合并，封顶 1。它未经统计校准，**不是放铳概率**；未观察到立直时使用基础威胁假设，不宣称对手未立直或未听牌。

## 输出与未知

观察器阶段增量：已支持 `initial_context.riichi` 入口事实；历史不全不再直接禁用条件打点，而按本人立直状态是否已确认判断。危险分项明确各家资格未知与库存缺失，见[观察器](observation.md)。

- `candidates[].metrics` 新增 lookahead_gain、conditional_value、danger、policy_score；details 保留分项、参数、假设、缺失项和逐家风险证据。
- `situation` 新增 attack_ranking、defense_ranking、overall_ranking；discard_ranking 指向综合排序。`recommendations` 可含 attack、defense、overall。
- 自家牌形不全：不排序。公开库存不全：仍比较已知牌形，进张和前瞻不可用；未知风、庄家、宝牌或资格历史时条件打点不可用，不用零冒充。
- 综合评分均为 conditional / heuristic；不输出尚未计算的和牌率、放铳率、净收益。鸣牌与立直现在参与动作比较；以下旧弃牌指标仍保留，统一动作输出见[动作扩展](action-v2.md)。

默认参数仅是可审阅的起点。后续可固定牌谱验证集对比权重，但不能用“与实战弃牌相同”证明更强，也不能把一局实际结果当作所有备选弃牌的收益标签。
