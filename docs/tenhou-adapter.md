# 天凤本地牌谱适配器

2026-11-13 增量：已接入 3 场真实七至九段凤凰桌示例牌谱，并新增带桌型、段位、终局、去重检查的本地批量入口。来源、数量、评测及授权边界见[高水平牌谱接入试验](high-level-replay-pilot.md)。

更新：2026-11-12。已实现完整四人有赤牌谱的本地 XML / gzip `.mjlog` 导入，不含网络下载器或模型训练。

```powershell
.\.venv\Scripts\python.exe -m nanikiru.adapters "D:\logs\game.mjlog" "D:\Nanikiru\data\game-import"
```

输出目录必须尚不存在，避免覆盖原数据。先解析并验证来源，再建立输出目录；遇到写盘错误可能留下部分输出，应处理该目录后重新导出。

每局生成 `hand-000-seat-0.json` 等四份玩家可见 RoundRecord，可用现有 `load_record`、`replay`、`build_analysis_request` 读取。当前通过程序接口回放和分析；未新增牌谱回放 UI，原人工事件窗口不支持这些导入事件，不应直接加载。`discard-samples.jsonl` 每行包含：

| 字段 | 含义 |
| --- | --- |
| schema_version | tenhou-discard-v1 |
| source_id / hand_index | 解压后 XML 的 SHA-256 来源标识、该来源中的局编号 |
| observer / event_count | 决策玩家及其行动前事件前缀长度 |
| input | 标准 AnalysisRequest，默认 fixed-v2，不包含标签 |
| labels.action | 实际 discard 或 riichi 动作及实体牌 ID |
| labels.outcome | 本局各次和牌/流局、原谱点数增减、已公开的终局信息 |

JSON 中 UNKNOWN 编为 `{"$unknown":true}`，不是 null。JSONL 是导出样本，不是新的模型张量规范或请求反序列化接口。程序内可以直接取得请求：

```python
from nanikiru.adapters.tenhou import load_tenhou
from nanikiru.analysis import analyze

for hand in load_tenhou("game.mjlog"):
    for sample in hand.samples(method_options={"risk": 10}):
        result = analyze(sample["input"])
        # sample["labels"] 单独用于评估，禁止回填 input。
```

## 事件与隔离

支持 INIT、四家摸打、REACH 两阶段、吃碰、大明杠/暗杠/加杠、DORA、AGARI（连续多家和牌）、RYUUKYOKU。赤牌由实体 ID 映射为 0m/0p/0s；鸣牌保留被鸣实体和来源，避免牌河与副露重复计数。暗杠/加杠先公开宣言，后遇到补牌或翻宝牌才确认；抢杠终局不错误地完成杠。

完整暗手只在适配器内部重建和校验。输出快照中对手暗手只有数量；对手摸牌事件隐藏牌码和 ID。SHUFFLE 种子、用户名等不透传。每个弃牌样本只提取行动前前缀；立直弃牌样本截在 REACH step 1 之前，避免泄露将要立直的意图。终局公开手牌和里宝可以出现在终局事件及 labels，但不会进入之前的 input。

来源哈希、round_id、state_version 是身份元数据，**不作为模型特征**。同一整场四家样本、不同局必须放在同一个训练/验证/测试分区；此外还需按来源去重，哈希不同不保证不是同一对局的不同格式副本。

## 规则与范围

按 GO 解析食断和场制，有赤四人采用每门一赤。条件算分按天凤的特殊单倍役满设置；支持显式传 `rules=RuleConfig(...)` 覆盖分析条件。天凤规则与本项目模拟器默认规则并非全同，例如三家和了流局、部分包牌/流局判定。适配器**保存来源事件与结算，不调用模拟器重裁原谱**；RoundSnapshot.rules 仅覆盖当前项目可表达的规则，不是天凤全规则等价声明。

三麻、无赤库存、REINIT 中途重连、缺失初始牌、缺终局、未知事件和不支持的模式明确拒绝，不猜补。当前只产出弃牌/立直弃牌样本；未把没有鸣牌当作显式 pass，不宣称恢复了玩家所有响应或振听资格。和牌/流局结算保留原谱值，不重新证明役种或合法性。未实现名次收益、对所有反事实弃牌的收益标签、训练划分器或网络模型。

格式核对依据：[天凤官方牌面解码脚本](https://tenhou.net/img/tehai.js)、[天凤官方规则](https://tenhou.net/man/)、[tenhou-log-utils 原始解析实现](https://github.com/mthrok/tenhou-log-utils/blob/master/tenhou_log_utils/parser.py)。其中官方说明确认特殊役满按单倍计，以及杠宝翻开时序等差异。本项目未引入这些项目为依赖。

## 验证

2026-11-14 的跨适配器事件加固、真实资料检查与复跑命令见 [适配器事件检查](adapter-audit.md)。

合成格式用例覆盖所有鸣牌、立直、翻宝、抢杠、多家和牌、前缀隔离、gzip、存档往返和不支持输入；合成数据不用于验证策略强度。另对 [MahjongRepository 的公开测试资料](https://github.com/MahjongRepository/tenhou-python-bot/tree/master/project/system_testing/fixtures) 中 1.txt—4.txt 做小样本兼容检查：共 30 局、1493 次弃牌，四家记录全部重建；1.txt 的 316 个训练请求逐个构建成功。未把外部牌谱加入仓库或批量收集训练集，未宣称任意年代/赛事牌谱均兼容。
