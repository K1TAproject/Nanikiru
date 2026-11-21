# 分析模块接口清单

更新：2026-11-11。本文列出当前可调用接口及后续功能边界；处理模块的数据交接见[输入输出契约](analysis-interface.md)，运行示例与限制见[分析模块说明](analysis-module.md)。标为“设计”的函数尚不存在，不可直接调用。

## 1. 对外入口与功能范围

观察器阶段增量：入口立直事实、最小分析历史、UNKNOWN 恢复、按字段依赖的弃牌许可见[观察器及输入契约](observation.md)。v1/v2 仍共用 AnalysisRequest；生成的数据集不直接交给状态推进器。

已实现对外入口 `analyze(request: AnalysisRequest) -> AnalysisResult`，按任务调用内部函数。basic-v1 支持 shanten/tenpai/discard_shape；minimal-shape-v1 和 fixed-v2 另支持 recommend 条件排序，当前只支持 shape 目标。fixed-v2 参数和指标见 [v2 说明](fixed-v2.md)。参数、返回键和限制见[分析调用链](analysis-pipeline.md)。不增加服务、插件注册或空实现。

请求沿用 `state_version、position、tasks、history、coverage、objective、method、method_options`，不重复传观察者或另一份牌局状态。历史只用于推导快照不能独立说明的资格，不重复推进快照。固定算法和模型共享这些事实语义，内部编码可以不同。

| 任务名称（含后续设计） | 功能与最低依赖 | 输出 | 当前进度 |
| --- | --- | --- | --- |
| shanten | 完整自家手牌和已成立组 | 当前向听 | 已接入调度 |
| tenpai | 13 张等价自家牌形；公开库存可选 | 理论等待、各牌未见数 | 已接入调度；不含和牌许可 |
| discard_shape | 14 张等价牌形、指定实体牌 ID；公开库存可选 | 各候选向听、改善牌、未见进张数 | 已接入调度；完整模拟/牌谱可见历史可确认弃牌许可 |
| hand_value | 和牌张、完整牌形、明确计分条件 | 有役与否、番符、基本支付 | 内部条件算分已实现 |
| legal_actions | 阶段、行动方、响应事件、手牌、相关历史/初始上下文 | 动作及 allowed/forbidden/unknown、依据 | 设计 |
| win_permission | 成形、有役、和牌来源；荣和还需振听/响应事实 | 自摸/荣和许可及禁止或缺失依据 | 设计 |
| riichi | 门清、点数、余牌、阶段、待弃牌和规则资格 | 立直/默听候选及条件评价 | 设计 |
| call | 可响应牌及来源、阶段、手牌、立直/杠约束 | 吃碰杠/跳过候选、成立后的评价 | 设计 |
| defense | 候选牌及对手可见行为，含必要时序 | 分对手安全依据；可选风险估计 | fixed-v2 的 recommend 已含启发式风险与防守排序；独立任务及概率为设计 |
| forecast | 满足模型要求的可见局面、终点和后续策略 | 和牌率、放铳率、期望净点数等 | 设计，无模型 |
| recommend | 合法候选及所需牌效/风险/价值结果、目标 | overall/attack/defense 推荐及理由 | 人工参数 v2 和最小牌效已接入调度，完整模拟观察时可推荐已确认合法弃牌 |

荒牌流局、途中流局和支付更新不能由牌效函数代办。终局判定、最终支付与下一局推进属于后续规则/场况功能；分析可以消费其结果或查询条件，当前不实现结算及状态写入。

## 2. 已实现接口及参数

### 共用约定

- `position`：`RoundSnapshot`，使用 `observer_seat` 的视角；不会修改调用者数据。
- `tiles/concealed`：牌码序列；`0m/0p/0s` 表示赤五。`groups` 是临时 `(kind, codes)` 序列，从统一牌表派生，不是第二套长期状态。组类型为 chi/pon/open_kan/closed_kan/added_kan。
- 每组按 3 张计算牌形等价数量，包括四张的杠。因此暗手数量为 `13 - 3 * 组数` 或 `14 - 3 * 组数`，已摸入牌在暗手中只计一次。
- `UNKNOWN` 是明确未知；空序列表示确实没有。非法牌码、实体库存超限、已知牌数错误或条件矛盾属于调用错误，不能当作正常未知；现有检查不保证发现所有规则矛盾。
- 面向模块的结果用 `Metric` 或 `CandidateResult`；底层原语仍可返回整数、集合、字典或 `UNKNOWN`，不为统一外观重复包装每个小工具。

### calculate_shanten(tiles, groups=())

位置：`nanikiru.analysis.efficiency`。输入 13/14 张等价牌形；返回整数或 `UNKNOWN`。`-1` 表示已成形，`0` 表示听牌形。无已成立组时取普通形、七对子、国士的最小向听；有组（含暗杠）时只算普通形。赤五按五计算形状，实体库存仍区分赤牌。

不要求场风、分数或牌河；不判断有役、振听、轮到谁行动或实际和牌许可。

### analyze_shanten(position) -> Metric

位置：`nanikiru.analysis`。要求自家 `hand:N`、`groups:N` 覆盖为 true，`untracked_hand_count=0` 且牌面已知。满足时返回 `available/exact` 整数；缺失时返回 `unavailable` 与 `details.missing_fields`。未知对手手牌、分数及公开库存不阻碍向听计算。

### analyze_waits(position) -> Metric

位置：`nanikiru.analysis`。要求完整的 13 张等价自家牌形；14 张等价时返回 `not_applicable`，应先指定弃牌或移出和牌张。返回 `available/exact` 的列表：

```text
[{tile: "3s", unseen: 3}, {tile: "6s", unseen: UNKNOWN}]
```

`tile` 为 34 类普通牌码，赤五合并到对应五。完整公开库存才能得到精确未见数，否则所有 `unseen` 为 UNKNOWN，等待本身仍可用。未听牌返回已知空列表。保留已公开耗尽、未见数为 0 的理论等待；不生成超过自家实体四枚限制的第五枚等待。该结果不是可荣和牌表。

### analyze_discard_shapes(position, tile_ids) -> tuple[CandidateResult, ...]

位置：`nanikiru.analysis`。`tile_ids` 为待比较的自家当前暗手实体 ID 序列，去重且保留首次顺序；不传牌码，赤牌与普通牌执行身份不能混淆。完整输入应为弃牌前 14 张等价牌形。

每项含 `Action(discard, observer_seat, (tile_id,))`，以及 `shanten/effective_tiles/ukeire` 三个 Metric。公开库存不足时向听与理论改善牌仍可计算，未见数未知、ukeire 不可用；自家牌形缺失时三项不可用。已知库存下改善牌排除未见数为 0 的牌，故与理论等待列表的用途不同。弃出的牌仍属于已见库存。

所有候选 `legality=unknown`：该接口只比较假设弃牌，不查立直锁定、食替或阶段，不排序、不生成 recommendations。非自家暗手 ID 抛出 ValueError。

### score_hand(...) -> Metric

位置：`nanikiru.analysis`。完整签名：

```python
score_hand(concealed, groups, win_tile, *, seat, dealer, round_wind,
           tsumo, riichi, indicators=UNKNOWN, ura=UNKNOWN,
           aka_dora_enabled=True, rules=None, **flags)
```

| 参数 | 含义及要求 |
| --- | --- |
| concealed | 不含 win_tile 的暗手牌码序列；13 张等价 |
| groups | 已成立组序列；无组显式传 `()` |
| win_tile | 本次假定和牌的单个牌码；保留赤牌身份 |
| seat / dealer | 和牌者、庄家的座位 0–3；自风由相对座位推出 |
| round_wind | east/south/west/north |
| tsumo | 必传 bool；true 自摸，false 荣和条件 |
| riichi | 必传 bool；声明在已成立/假定成立立直条件下算分，不代替资格检查 |
| indicators | 已揭示表宝牌指示牌序列；不是宝牌本身。UNKNOWN 不可精确算分，`()` 明确假设没有 |
| ura | 里宝指示牌序列；立直时需要已知或明确假设。非立直时不计，UNKNOWN 不影响结果 |
| aka_dora_enabled | bool，默认 true；只控制赤宝计分，不改变实体库存 |
| rules | RuleConfig；应传 position.rules 以共用本局规则，显式 rules 优先于旧 aka_dora_enabled 参数；默认特殊双倍役满关闭 |
| flags | 以下特殊条件，省略均按 false，并在输出中列明；UNKNOWN 导致不可用 |

支持的 flags：`is_daburu_riichi`（两立直）、`is_ippatsu`（一发）、`is_rinshan`（岭上）、`is_chankan`（抢杠）、`is_haitei`（海底）、`is_houtei`（河底）、`is_tenhou`（天和）、`is_chiihou`（地和）。不接受其他名称。不能把省略条件称为“已从历史证实为否”；这是调用者选定的条件。

| 情况 | 返回 |
| --- | --- |
| 所需条件未知 | unavailable，missing_fields 列明缺失 |
| 合法牌数但未构成和牌形 | not_applicable，reason=not_a_winning_shape |
| 已成形但在指定条件下无役 | conditional/exact，`{has_yaku: false, score: None}` |
| 指定条件下有役 | conditional/exact，`{has_yaku: true, score: {han, fu, cost, yaku, fu_details}}` |
| 已检查到条件矛盾或非法输入 | ValueError；不伪装成无役 |

`cost` 沿用 mahjong 2.0.0 的支付字段：荣和 main 为放铳者基本支付；非庄自摸 main 为庄家支付、additional 为每位闲家支付，庄家自摸各家支付相同。不是赢者净收益，也不含本场、供托、包牌或多家和牌结算。details 明示全部条件及 `win_permission_checked=false`。

### rank_discard_candidates(candidates) -> Metric

位置：`nanikiru.analysis.minimal_policy`，已实现。参数为同一局面的 CandidateResult 序列，可直接复用 analyze_discard_shapes 的结果，避免重新计算牌效。拒绝重复候选和非弃牌动作，排除 forbidden，保留 unknown 但不修改合法性。

返回按优先级排列的并列层，每层为 Action 元组：`((最佳候选A, 最佳候选B), (次优候选C,), ...)`。先最小向听，再最大未见进张数；同分保留并列和原输入顺序，不额外按赤牌/宝牌/打点破同分。

- 向听或进张均已知：available/heuristic；牌形数值本身精确，排序目标仍是启发式。
- 任一候选进张数未知：conditional/heuristic，全体仅比较向听；details.criteria 与 missing_fields 明示降级，不用零填补。
- 任一候选向听未知：unavailable；无可比较候选：not_applicable。

第一层是牌效偏好，不是完整策略或和牌许可。调度器可根据完整模拟观察的共享弃牌约束授予 allowed，并将合法第一层放入 recommendations；人工历史不足仍 unknown。函数不生成 AnalysisResult.recommendations、不将 unknown 改为 allowed。测试 UI 在明确的普通摸牌假设下展示这些条件推荐。

## 3. 保留的底层工具

下表中的 efficiency 位于 `nanikiru.analysis`，scoring/call_rules 位于 `nanikiru.rules`。基础查询由 `nanikiru.analysis` 包导出，实现在 `analysis/basic.py`；见[项目架构](architecture.md)。

| 工具 | 用途 |
| --- | --- |
| efficiency.hand_codes / meld_codes | 从快照提取完整自家牌形或 UNKNOWN |
| efficiency.visible_inventory / visible_counts | 已知实体计数 / 覆盖完整的 34 类库存；前者不保证覆盖完整 |
| efficiency.analyze_shape / analyze_discards | 原始牌形字典；模块层包装复用它们 |
| scoring.waits | 13 张等价牌形的等待索引集合 |
| scoring.ron_restrictions | 组合已提供的舍牌/临时/立直振听条件；不从历史自动推导 |
| scoring.evaluate | 明确条件番符原语；不成形或无役返回 None，非法条件报错 |
| call_rules.call_banned | 对已确定吃碰计算食替牌类；不判断是否可以吃碰 |

## 4. 后续内部接口的最小划分（设计）

| 拟定接口 | 参数与结果 | 实现前还需确定 |
| --- | --- | --- |
| query_legal_actions(request) | 同版本快照、必要历史/coverage → CandidateResult 序列；合法性及依据 | 事件载荷、响应窗口、初始资格事实、跳过和牌与摸牌边界 |
| analyze_win_permission(request, action) | 指定 ron/tsumo 候选 → 许可 Metric | 当前和牌来源、振听与有役依据；未知不能当允许 |
| evaluate_action(request, action) | 指定候选 → CandidateResult，各项条件评价 | 吃碰后的合法弃牌分支、杠未决状态和立直成立时点 |
| analyze_safety(request, candidates) | 候选与公开时序 → 分候选、分对手的依据 Metric | 安全依据有效时间及适用和牌方式；启发式不报伪概率 |
| forecast(request, candidates, *, horizon, continuation_policy) | 当前局面/候选后的局面 → 预测 Metric 映射 | 训练数据、目标定义、缺失模式、模型版本及校准 |
| rank_candidates(request, candidates) | 已有合法性和评价 → 分策略动作元组 | 不同目标的评分准则；只能推荐 allowed 候选 |

以上复用 request，不把所有历史相关资格复制为可写 PlayerInfo 字段。按任务只读取所需信息；未知阻断相关结论，不阻断其他任务。暂不为各类预测/评分引入通用插件层。

预测须分别定义“本局结束前自己和牌率”“现在弃该牌对某人/任意一家放铳率”“指定终点净点数期望”。target、horizon、continuation_policy、模型版本应随结果返回；任意一家风险不能直接相加，各种概率也不能用策略 softmax 代替。当前无此类数值输出。
# 2026-11-13：动作分析增量

`analyze(request)` 在 `fixed-v2 + recommend` 下调用 `evaluate_actions`，统一输出动作候选；内部 `evaluate_routes` 评价路线，`additional_actions` 推导可见行动许可。内部函数不是新持久化格式。字段、覆盖范围和限制见[动作扩展](action-v2.md)。

