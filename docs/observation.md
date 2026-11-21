# 完整与不完整数据观察器

已实现，2026-11-12。观察器位于 `nanikiru/observation/`，输入是**已经按玩家视角提取的 AnalysisRequest**。它不接收全知牌山，不实现视觉识别，也不负责猜补未知。原三个 UI 未修改；本阶段提供程序接口、批量命令和可复现实验。

## 单局面调用

```python
from nanikiru.observation import ObservationConfig, observe_request
from nanikiru.analysis import analyze

partial = observe_request(request, ObservationConfig(
    midgame=True, public_code_rate=0.25, discard_kind_rate=0.5), seed=42)
result = analyze(partial)
```

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| midgame | false | 随机选一个已有事件之后作为接入时点，删除之前历史，保留当前快照 |
| history_start | None | 显式指定删除的历史事件数量，优先于随机接入；0 不删除，历史长度表示删除全部 |
| public_code_rate | 0 | 公共实体牌码独立遮蔽率；同时处理当前牌表及保留历史中的同一实体 |
| hand_code_rate | 0 | 自家当前暗手牌码遮蔽率；其历史引用同步遮蔽 |
| discard_kind_rate | 0 | 手切／摸切方式遮蔽率；同时隐藏此前摸牌身份和牌码，防止凭摸打 ID 相等恢复方式 |
| hidden_rivers | () | 指定玩家牌河的牌码全部未知，公共覆盖标不完整；仍保留已定位的牌槽和数量 |
| hide_order | false | 删除全部历史并打乱牌河记录顺序，表示无法确认此前弃牌时序；保留当前可见牌及宝牌指示牌槽位 |
| hide_riichi | false | 去除立直确认信息，接入立直资格设 UNKNOWN，不以未记录当未立直 |

随机过程可复现，不修改输入。多个来源／时点的批量种子由来源、局号、观察者、事件数量和全局种子稳定生成。这里的遮蔽是**同一样本内持续缺失**，不是“先看清，后来被挡住但还能记得”的记忆模型。组内其他牌可能仍允许推断未知牌；本阶段保留 UNKNOWN，不自行做逻辑补牌。

`hidden_rivers` 只模拟已定位但未读清的牌槽，不模拟未知牌槽数量、整组漏检或玩家归属混淆。错误识别与互相矛盾的数据不属于本观察器，应由后续处理模块纠错。

## 信息隔离

1. 当前快照不再附带完整快照变化。历史转换为 `observed_*` 的最小 `fact`，只保留已定义的牌引用、牌码、鸣牌来源和动作属性，不透传 `changes` 或自定义载荷。
2. 遮蔽牌码同步作用于历史中的同一实体；不能从过去的 draw/discard/kan 事实直接找回被隐藏的码。
3. 天凤实体 ID 能编码牌类，因此输出全部牌引用重新编号为不含牌类语义的局部 ID。标签中的实际弃牌同步换为这些 ID。
4. 立直成立在接入之前时，仅携带接入时确认的状态，不制造一条假时间的历史事件。旧牌河仍可见，但不虚构它与立直的先后关系。
5. 未知、None 和空集合继续区分。历史不完整不代表当前公共库存一定不完整；手摸切不清不意味着牌种也不清。

`observed_*` 是分析事实，**不是可回放的状态变化事件**，不能交给 `RoundRecord` 推进器重演。持久化使用下述样本 JSONL；原始权威记录仍留在原文件中。

## 输入契约增量

请求仍共用 AnalysisRequest，不另建 v1/v2 输入。唯一新增的接入上下文规范为：

```python
position.initial_context = {"riichi": [False, True, UNKNOWN, False]}
```

依次对应 0—3 家，含义是**保留历史开始之前**已确认的立直成立状态；不是当前资格缓存。后续 riichi_accepted 事实覆盖入口的 false/unknown。缺键表示四家均未知。其他未定义键拒绝；不接受任意扩展信息。历史有缺口时，入口 false 不自动推断为当前 false；已成立立直的 true 则保留。

弃牌许可按需要的事实查询：

- 自家牌形、当前阶段与行动方须明确。
- 已确认立直＋当前摸入实体已知，可确认仅摸切许可。
- 已确认未立直，还需要保留的最新本人摸牌／吃碰事实；只剩快照时不假装排除了未决宣言或食替约束。
- 吃碰后的相关牌种不清时，不把食替集合当作空集合。

因此，**中途接入不再一律拒绝，但也不一律允许**。保留的历史和入口事实足够时可以给可执行推荐，否则只有条件排序。

v1 保持向听／进张依赖分离。v2 使用入口立直状态计算威胁和条件打点，风险 details 新增各家 riichi 及缺失字段；缺公开牌只禁用依赖精确库存的项，其他计算继续，风险仍为 conditional/heuristic，不是校准概率。

`contracts.request_from_dict()` 可恢复已导出的标准请求：还原 `$unknown`、规则和嵌套结构，再做契约验证。它不是全知输入净化器。state_version、round_id、来源哈希和本地实体 ID 均为身份信息，不应作为网络预测特征。

## 批量生成与分片

先使用天凤适配器导出 `discard-samples.jsonl`，再运行：

```powershell
.\.venv\Scripts\python.exe -m nanikiru.observation data\game1\discard-samples.jsonl data\game2\discard-samples.jsonl --output data\observations --seed run-1 --shard-size 1000 --audit-limit 20
```

默认每个决策生成 complete、midgame、discard_kind_unknown、public_missing、mixed 五种观察。可以用 `--config variants.json` 替换，例如：

complete 表示不新增遮蔽，不会修复来源本身已有的缺失。

```json
{
  "complete": {},
  "midgame": {"midgame": true},
  "unknown_hand": {"hand_code_rate": 0.15},
  "river_1_hidden": {"hidden_rivers": [1]},
  "order_unknown": {"hide_order": true}
}
```

输出目录必须新建。支持 `tenhou-discard-v1` 和具有相同元数据／input／labels 结构的 `discard-sample-v1`。后者可用于模拟器或其他已经确认的可见来源。

- 逐行读取，每个样本逐变体生成；不会把整个语料库加载到内存。单行本身仍须能装入内存。来源天凤 XML 的首次解析仍以单份文件为单位，本阶段未改为流式 XML。
- 输出为 `train-00000.jsonl`、`validation-00000.jsonl`、`test-00000.jsonl` 等分片，按来源 ID 哈希近似 80/10/10 划分。同一来源的四家、所有局和所有变体必在同一分区；小样本不保证三个分区都有数据。
- 每行 `observed-discard-v1` 包含 input、独立 labels、source_id／hand_index／observer／event_count、variant、split、遮蔽配置及种子。
- `manifest.json` 记录版本、参数和实际数量。异常时停止并标记 incomplete，不静默跳过，也不覆盖旧数据；不支持断点续写。
- `audit.jsonl` 对前 N 个原始样本的所有变体实际调用 v1/v2，记录首选牌和逐候选指标状态。`--audit-limit 0` 可关闭，避免大语料导出时逐条运行昂贵分析。
- 不自动去重。同一对局不同格式可能有不同来源哈希；生产训练前仍需按真实对局身份清洗。不要跨分区复制变体，也不要把 labels 或遮蔽元数据拼进输入特征。

这里已经可以产数据，但尚未实现神经网络编码、训练器、概率校准或未知牌枚举。

## 已执行的对照实验

```powershell
.\.venv\Scripts\python.exe -m examples.observe_analysis --output 新实验目录
```

使用 2 个固定模拟来源、16 个弃牌时点生成 80 个观察样本，并对每个样本运行两个方法。结果及逐项证据见 [实验报告](observation-checks/report.md)、[导出清单](observation-checks/observations/manifest.json)、[分析审计](observation-checks/observations/audit.jsonl)。实际动作是测试推进标签，不是高手或最优策略标签，不能据此评估强度。
