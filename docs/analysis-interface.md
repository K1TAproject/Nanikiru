# 处理模块与分析模块接口

更新：2026-11-11。状态：接口设计及 Python 数据契约已落地，已有基础分析内部函数、最小牌效偏好排序和独立单局面测试 UI；同步基础调度已实现，见[分析调用链](analysis-pipeline.md)；完整视觉处理器、综合动作评价、概率预测尚未实现；新模拟器的完整可见观察已支持共享弃牌许可，见[模拟器说明](simulator.md)。数据入口：`nanikiru/models.py`、`nanikiru/contracts.py`。函数参数与实现状态见[分析模块接口清单](analysis-api-list.md)，功能说明见[分析模块说明](analysis-module.md)。

## 1. 职责与一致性

处理模块将识别观察整理为无矛盾的玩家可见事实，负责去重、跨帧关联、冲突解决、历史修订及版本。分析模块基于事实计算规则资格与策略结果，不回看图像、不自行纠正牌面、不选择相信哪次识别。

一致不等于完整。UNKNOWN 是明确未知；None 是没有/不适用；已知空列表不能代替信息缺失。矛盾输入属于接口错误，不作为正常的未知输入。构造器的结构检查不能代替完整处理器和麻将规则校验。

已知手牌、历史和当前快照必须属于同一版本。快照已经包含历史的作用，分析模块不能将历史重复应用。人工记录提取器只处理原观察者的事件前缀，过滤已定义扩展容器并保留本人的响应；initial_context 仅接受 `riichi` 四家 bool/UNKNOWN 的接入时事实，其他键拒绝。它不是能净化任意全知牌谱的安全转换器，具体边界见分析调用链文档。

## 2. 分析请求

Python：`AnalysisRequest(state_version, position, tasks, history=(), coverage=..., objective="shape", method="minimal-shape-v1", method_options={})`。

| 字段 | 含义 |
| --- | --- |
| state_version | 不透明的局面修订标识；纠正或推进后产生新值 |
| position | RoundSnapshot；含 observer_seat、rules（RuleConfig）、规则标识、牌表、组关系、场况、行动上下文 |
| tasks | 请求的任务名称，如 discard_shape、tenpai、defense、riichi、win_permission、forecast |
| history | 截至当前时点的已确认可见事件；不是原始识别记录 |
| coverage | from_start 与 gaps，记录是否从开局完整及已知缺口；初始已知事实在 position.initial_context |
| objective | shape（牌形）、hand_value（本局收益）、match_rank（整场排名）；当前调度仅支持 shape |
| method | 方法及版本，默认 minimal-shape-v1；另实现 basic-v1、fixed-v2 |
| method_options | 方法参数字典；目前仅 fixed-v2 支持，字段见 [v2](fixed-v2.md)，其他方法须为空 |

概念上的 context 字段已展开为 state_version、tasks、objective；观察者只在 position 中保存一次。接口不重复传输完整存档及其初始快照，也不强制传输派生候选列表。后续规则查询可以在分析模块内生成动作；当前已实现完整模拟观察下的弃牌约束查询，其他动作查询任务尚未接入。

当前调度支持 shanten、tenpai、discard_shape 和 minimal-shape-v1 / fixed-v2 下的 recommend 条件排序；其他任务仅为设计。match_rank 目标可以表达，但完整赛制/整场进度输入尚待定义，目前不得宣称支持整场最优推荐。

### 未知与集合覆盖

Python 使用 `UNKNOWN`（枚举值），不使用字符串 `"unknown"` 作为构造器中的替代对象；None 不表示未知。直接将 `dataclasses.asdict` 交给 JSON 会把枚举输出为字符串，不能据此恢复未知身份。新的人工记录文件使用专用 `{"$unknown": true}` 编码，加载/回放接口见[人工推进说明](manual-progression.md)；无旧格式迁移器。

RoundSnapshot 新增 `completeness` 作为集合覆盖事实，不是资格缓存：

| 键 | true 的含义 |
| --- | --- |
| hand:N | N 的当前暗手没有漏记区域；牌面仍可以 UNKNOWN，未建档数量仍单独表达 |
| groups:N | N 的已成立组没有整组漏记 |
| public_tiles | 当前已公开的物理牌没有漏记；不代表知道对手暗手或牌山 |

false 表示确认不完整，缺键/UNKNOWN 表示无法确认完整。组内牌面未知可用占位 TileRecord 表达；整组都未观察到时 groups:N 不能标 true。

入口上下文已规范 riichi，新增 observed_* 最小可见分析事实，详见[观察器契约](observation.md)。其余覆盖区间、pending 和事件 data 具体事件载荷的全部校验、延迟翻宝/任意动作中接入、修订协议留待下一阶段。不能将这些开放容器误认为完整事件引擎。

## 3. 场景输入与降级

“必须”针对某项结论，不是调用接口前必须知道全部字段。未知不拖累无关的分析；不支持枚举/估计时返回不可用，不猜测填补。

| 场景 | 最低已知信息 | 可选增强 | 未知时的处理 |
| --- | --- | --- | --- |
| 普通弃牌牌形 | 完整自家暗手、鸣牌、待弃牌时点 | 公开牌、场况、历史 | 缺自家牌不能给唯一确定牌形；缺公开牌仍可算向听 |
| 进张未见枚数 | 牌形与完整已知牌覆盖 | 更完整历史 | 覆盖不足时不输出精确 ukeire；理论牌形改善仍可计算 |
| 可执行弃牌 | 手牌、阶段、立直/食替依据、必要摸入牌身份 | 牌效/防守/价值 | 关键约束未知时仅作条件比较，不能标 allowed |
| 吃碰大明杠 | 响应牌与来源、阶段、自家牌、立直约束；杠补牌条件 | 宝牌、余牌、威胁 | 无法确认资格时只能分析假设成立后的局面 |
| 吃碰后弃牌 | 已成立组合、鸣入牌、剩余暗手、阶段 | 全部公开信息 | 不得丢失食替依据而当普通摸打 |
| 暗杠/加杠 | 所用牌、原碰、摸牌上下文、立直及补牌约束 | 宝牌、预测收益 | 抢杠未决不能先计杠成立；历史比较不全时资格未知 |
| 立直/默听 | 手牌、门清、点数、余牌门槛、阶段 | 风、宝牌、威胁、局进度 | 缺资格事实时立直仅为条件候选；缺计分事实不报完整打点 |
| 荣和/自摸许可 | 完成形、和牌张与来源、阶段、有役依据；荣和振听依据 | 完整计分条件 | 已知禁止条件可确认禁止；未知限制未排除时不可确认允许 |
| 听牌质量 | 完整自家暗手和组 | 公开库存、振听、场况 | 等待、未见、荣和许可、条件打点分别返回 |
| 防守依据 | 候选牌、相关公开弃牌/行为 | 立直时点、摸切、点差 | 无依据即未知，不等于安全 |
| 放铳风险预测 | 候选与满足该模型要求的可见局面 | 历史与场况 | 模型未训练支持该缺失模式则不可用 |
| 和牌率/期望收益 | 可见局面、预测终点、后续策略与模型要求 | 历史、对手行为 | 标为估计，不从进张数直接换算概率 |
| 攻守选择 | 候选与风险/收益或启发式依据 | 点差、整场进度、排名目标 | 缺整场信息不能声称排名最优 |
| 非本人行动 | 观察者、阶段、公开局面 | 可见历史 | 允许局势分析，不给立即弃牌执行建议 |
| 结算 | 相关揭示手牌、终局结果、场况规则 | 整场结果 | 缺听牌/支付依据不能生成完整确定结算 |

## 4. 输出契约

Python：`AnalysisResult(state_version, method, situation={}, candidates=(), recommendations={})`。

| 字段 | 含义 |
| --- | --- |
| state_version | 与请求一致；交互/处理模块拒绝将旧版本当实时建议 |
| method | 方法及版本，不同固定算法/模型可分别返回一份结果 |
| situation | 当前局势指标，名称到 Metric 的映射 |
| candidates | CandidateResult 列表：action、legality、metrics、reasons |
| recommendations | overall/attack/defense 等到并列推荐动作元组的映射；缺键表示该策略未计算 |

Action 包含 kind、actor、tile_ids、target_id。支持 discard/riichi/chi/pon/open_kan/closed_kan/added_kan/pass/ron/tsumo/abort_nine。弃牌/立直用一个手牌记录 ID，保留赤牌和摸入牌执行身份。吃碰杠的 tile_ids 表示本方贡献的实体牌；外部牌由 target_id 对应事件关联。动作合法性由规则查询确定（当前分析侧根据完整历史，或入口立直事实加保留的相关动作，确认弃牌限制），类型构造并不授予行动许可。

legality 为 allowed/forbidden/unknown。可执行 recommendations 只能引用本次 allowed 候选；条件候选留在 candidates 中并说明条件，不混入立即执行建议。

Metric 包含：

| 字段 | 含义 |
| --- | --- |
| status | available / conditional / unavailable / not_applicable |
| value | 实际值；不可用/不适用时为 None，不能用 0 冒充 |
| basis | exact / heuristic / estimate；有值时必须注明 |
| details | 必要条件、缺失字段、计算/模型来源与口径；暂不强制通用 confidence |

不同指标可独立受限。缺失指标键表示未请求/未计算；请求了但无法提供的指标应返回 unavailable 及原因。概率不能冒充客观正确率；未知输入程度、预测概率与统计置信区间不能混为一种置信度。

### 可选指标的明确含义

| 指标 | 口径 |
| --- | --- |
| shanten / effective_tiles / ukeire | 指明当前或候选执行后的状态；未见库存不是活牌山 |
| waits / win_permission / hand_value | 理论等待、许可、条件番符与支付分开 |
| win_probability | 当前或先执行候选后，按指定后续策略，在本局终止前自己和牌的概率 |
| deal_in_probability | 现在弃该牌，对指定玩家或任意一家放铳的概率；多人事件不能直接求和 |
| safety_evidence | 某对手的公开安全依据；不等同于估计放铳概率 |
| expected_deal_in_loss | 放铳概率与支付联合形成的期望损失 |
| expected_score_delta | 指定终点的预期净点数变化；不是条件和牌打点 |
| attack/defense/overall recommendations | 分目标/策略候选，不用一个无量纲百分比混合牌效与风险 |

概率预测 details 必须说明 horizon、continuation_policy、target（对手/全桌）、模型版本及适用条件；数值有无校准须按评测结果说明，不能由策略 softmax 推导和牌率。模型输入不得包含终局结果或未来公开牌；未来收益仅可作为训练标签。

## 5. 本轮实现边界和保留工具

底层适配阶段建立结构和契约，保留以下可独立计算的工具；2026-11-11 新增 `analysis/basic.py` 内部指标接口，随后已接入统一调度：

- `tiles.py`：牌码、34 种索引、赤牌库存。
- `analysis/efficiency.py`：hand_codes、meld_codes、visible_inventory、visible_counts、calculate_shanten、analyze_shape、analyze_discards。
- `analysis/basic.py`：analyze_shanten、analyze_waits、analyze_discard_shapes、score_hand；返回 Metric 或 CandidateResult。
- `rules/scoring.py`：waits、ron_restrictions、evaluate 及组形检查。
- `rules/call_rules.py`：基于牌码的吃碰食替集合。

其中 analyze_discards 是候选牌形工具，返回 tile_id、shanten、effective_tiles、ukeire；不生成合法动作、不返回最终推荐，也不是完整 AnalysisResult 引擎。未知手牌/组返回 UNKNOWN 牌形结果；公开库存不全时仍计算向听与理论改善，ukeire 为 UNKNOWN。

evaluate 接收牌码、临时 `(kind, codes)` 组、和牌张及明确条件，不接收旧 PlayerState。暗手参数不含和牌张。省略的特殊和牌标志表示按 false 条件计算；传 UNKNOWN 会被拒绝，调用者不得把它称为未知局面的完整实际算分。输出不包含振听/响应合法性、本场供托和包牌结算。默认原规则基准，不声称覆盖所有变体。

模块层 score_hand 在调用 evaluate 前处理 UNKNOWN，返回 unavailable 及缺失条件；成形但无役与未成形分别表达，已检测到的非法条件抛出 ValueError。有值时依旧是 conditional，不是实际和牌许可。

`analysis.minimal_policy.rank_discard_candidates` 已实现独立牌效排序，不修改候选合法性、不填入可执行 recommendations。单局面测试 UI 在明确假设下展示排序结果。

processing 已实现有限的摸打/响应结束/暂停事件应用，以及撤销、保存加载与回放。其他事件载荷、资格历史推导、合法动作生成、综合策略/概率预测、图像处理留待后续。analyze() 已调用实际基础函数和最小排序；没有综合策略或模型占位引擎。
# 2026-11-13：v2 动作输出增量

`fixed-v2` 的 `recommend` 现包含未听牌路线和动作比较，契约见[动作扩展](action-v2.md)。`candidates` 与 `recommendations` 不再保证只有弃牌；动作可没有牌 ID。`discard_ranking` 仍只比较弃牌，`action_ranking` 比较动作，未知许可不得执行。输入结构不变。

