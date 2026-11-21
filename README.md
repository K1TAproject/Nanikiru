# Nanikiru

项目交接与论文回顾入口：[研发记录、失败与实验索引](docs/research-log.md)。包含当前目标、历史决策、结果指针和下一步；各旧阶段说明以此处链接的实际版本／报告核查为准。

Git 日常同步及本地数据边界见 [Git 同步说明](docs/git-sync.md)。

路线特征、可重复牌谱评测与固定 v2 权重拟合已实现，使用方法和当前边界见[训练说明](docs/v2-training.md)。

Python 四人日麻画面辅助项目。最终方向是“获取画面 → 识别与处理对局 → 分析 → 给出何切建议”，支持固定计算和神经网络等分析方法。

**当前阶段（2026-11-21 核查）：可见信息分析、A 规模拟与批量对战评测。** 已有 v1／启发式 v2、天凤 XML 与 MJAI 适配、完整／缺失观察数据、独立测试界面和机器人牌桌；arena 支持完整半庄评测并已接入 akochan。旧 v2 已进行弃牌／立直拟合试验，不能视为全动作顺位优化。新增最终收益导出、离线条件后继分支和共享顺位价值模型；共享模型仍未训练，等待用户确认设置。视觉识别、可见信息下的完整分支概率与在线全动作顺位规划尚未实现。各阶段能力与验证以研发记录为准。

## 文档

- [批量半庄评测与外部基准](docs/batch-evaluation.md)
- [共享顺位模型：设置、参数与未训练边界](docs/shared-rank-value.md)
- [候选与条件后继分支](docs/successor-branches.md)

- [完整/不完整观察器：输入契约、批量分片与实验](docs/observation.md)

- [人工参数 v2：评分、权重、未知与使用](docs/fixed-v2.md)
- [天凤牌谱适配：导入、样本与数据隔离](docs/tenhou-adapter.md)

- [连续单局模拟器：规则、接口、记录与结算](docs/simulator.md)
- [新对局 UI：操作与调试](simulator_ui/README.md)
- [设计入口](docs/design.md)
- [项目架构与后续开发归属](docs/architecture.md)
- [五结构初步方案及最新落地状态](docs/observation-data-design.md)
- [处理/分析输入输出接口与场景表](docs/analysis-interface.md)
- [分析模块接口清单：参数、结果及实现状态](docs/analysis-api-list.md)
- [分析调度与统一输出：接口、使用和边界](docs/analysis-pipeline.md)
- [分析模块说明：当前功能、未知降级与运行示例](docs/analysis-module.md)
- [单局面测试 UI：启动、随机/手动组牌与图形素材](test_ui/README.md)
- [人工事件推进：事件、记录、版本及边界](docs/manual-progression.md)
- [独立事件录入工具：操作说明](event_ui/README.md)
- [本轮删除、保留、兼容性与验证边界](docs/direction-adaptation.md)
- [固定方法与神经网络方向](docs/training-product.md)
- [历史规则基准](docs/rules-and-settlement.md)、[第三方依赖](docs/third-party.md)

## 当前代码

| 文件 | 职责 |
| --- | --- |
| nanikiru/simulation/ | 连续单局推进、结算、全知存档及玩家观察投影 |
| nanikiru/rules/config.py、discards.py | 共用规则设置及弃牌约束 |
| simulator_ui/ | 新独立牌桌 UI、四家人工操作、回放与分析 |
| nanikiru/models.py | TileRecord、PlayerInfo、RoundSnapshot、Event、RoundRecord；UNKNOWN 与结构检查 |
| nanikiru/contracts.py | AnalysisRequest/Result、Action、CandidateResult、Metric；包含分析方法选择 |
| nanikiru/analysis/basic.py | 向听、等待、指定弃牌牌效与条件打点的指标接口；从 analysis 包导出 |
| nanikiru/analysis/fixed_policy.py | 人工参数 v2；条件打点、后续牌效质量、风险与攻防排序 |
| nanikiru/observation/ | 完整/缺失观察、批量数据分片及 v1/v2 对照审计 |
| nanikiru/adapters/ | 天凤本地 XML/gzip 转四家可见记录及分离标签样本 |
| nanikiru/analysis/minimal_policy.py | 最小向听优先、最大未见进张次之的并列排序；不授予行动许可 |
| nanikiru/processing/analysis_input.py | 人工记录前缀提取与标准请求构造 |
| nanikiru/analysis/dispatcher.py、output.py | 同步方法调度、统一结果格式化和调试 JSON |
| test_ui/ | 新单局面图形测试工具；与分析核心分离 |
| nanikiru/processing/ | 确认可见事件、阶段与库存变化、保存加载/回放/撤销 |
| event_ui/ | 独立人工事件录入窗口，原 test_ui 保持不变 |
| nanikiru/tiles.py | 牌码及 34 索引、赤牌物理库存 |
| nanikiru/analysis/efficiency.py | 观察库存、向听/进张、指定弃牌的牌形计算 |
| nanikiru/rules/scoring.py | 条件番符、等待、振听条件组合；不判断实际和牌许可或最终结算 |
| nanikiru/rules/call_rules.py | 吃碰后的食替集合 |

牌码 `0m/0p/0s` 保留赤牌身份，无独立 red 字段。当前规则标识 `riichi-4p-v1` 采用每门一赤三普通五的库存；关闭赤宝计分不改变实体库存。

未知用 `UNKNOWN`，没有/不适用用 `None`；集合完整性另行标记。资格由后续计算推导，不在 PlayerInfo 重复保存。处理器支持有限事件的回放与新格式加载，结构与阶段检查不是完整合法性验证器。

已按职责整理 `analysis/`、`rules/`、`processing/`，逻辑模块不对应单个文件。处理只维护事实，分析只读事实；UI 不自行推进核心状态。

## 运行与验证

Python 3.11+，依赖固定为 `mahjong==2.0.0`。本机使用 `.venv\Scripts\python.exe`，从项目目录调用无需安装项目本体。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m examples.inspect_position
.\.venv\Scripts\python.exe -m examples.analyze_basics
.\.venv\Scripts\python.exe -m test_ui
.\.venv\Scripts\python.exe -m event_ui
.\.venv\Scripts\python.exe -m simulator_ui
.\.venv\Scripts\python.exe -m examples.simulate_round
```

2026-11-12：**121 项单元测试通过**（观察器阶段新增 12 项，此前 109 项），Python 3.11.4、mahjong 2.0.0。v2 为可解释启发式，尚无强度或概率校准结论；天凤适配另做了 4 份公开样例、30 局的重建检查。

模拟器、事件分析面板、人工推进和原单局面 UI 的交互检查通过；已实际查看 v2 紧凑布局并验证摘要可滚动查看。原 `test_ui/` 七个文件的 SHA-256 均与本轮开始一致。

旧 `run_game.py`、`run_debug.py` 和旧 Tk 界面已删除。单局面入口为 `python -m test_ui` / `run_position_ui.py`，事件入口为 `python -m event_ui` / `run_event_ui.py`。旧 API/存档不兼容；[旧 JSON 样例](examples/README.md)原样保留，没有自动迁移。新模拟器已实现吃碰杠/立直、和牌及流局结算；人工事件工具仍只支持其原有有限事件。分析侧共享弃牌约束，吃碰杠/立直策略评价与概率模型待后续开发。
# 2026-11-13 开发增量

v2 已支持未听牌进攻路线与弃牌、立直、吃碰杠、和牌及跳过的动作比较；在现有模拟器／事件工具中选择 `fixed-v2` 查看。见[实现与边界](docs/action-v2.md)。原单局面 UI 未修改。


MJAI（JSONL/gzip/zstd/tar）牌谱导入及训练入口见 [MJAI 适配器](docs/mjai-adapter.md)。

2026-11-13 MJAI 适配验证：全套 144 项测试通过；实际 10 场 / 110 局导出 120 条样本并完成训练特征缓存接入。本次未改变 UI，未重新进行桌面检查。

天凤四人半庄训练目标、规则差异及导入检查见 [规则核查](docs/tenhou-rules.md)。模拟器可选新预设；正式训练须重导旧样本、重建缓存。

第一轮真实牌谱调参的划分、进度与结果路径见 [实验说明](docs/v2-round1.md)。

训练进度窗口：python -m training_ui（或 run_training_ui.py），显示真实分片进度、错误、参数搜索与损失曲线。见 [说明](docs/training-monitor.md)。

2026-11-14：独立[机器人四方牌桌](docs/robot-table.md)已实现，运行 `python -m table_ui` 或 `run_table_ui.py`。各家可选择人工、v1、默认 v2 或本轮拟合 v2；支持单步／自动推进、旁侧主视角分析、保存／撤销和单局结算。全套 176 项测试通过。

2026-11-15：新增 [A 规批量半庄评测器](docs/batch-evaluation.md)，支持多机器人配置、akochan 本地接入、四座位轮换、失败现场、断点续跑与配对版本比较。入口 `python -m nanikiru.arena examples/evaluation-akochan.json --output data/evaluations/my-run`。本阶段不修改分析器参数。
