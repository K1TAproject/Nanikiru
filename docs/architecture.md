# 项目架构与开发归属

更新：2026-11-11。架构调整后已实现人工事件推进最小闭环，见[推进说明](manual-progression.md)。逻辑模块可以由多个文件组成；以下区分实际目录与后续开发归属。

## 当前目录

```text
nanikiru/
  __init__.py                 基础结构的公共导出
  models.py                   五种观察/记录结构、UNKNOWN、结构检查
  contracts.py                请求、结果、候选、指标契约
  tiles.py                    牌码、实体库存、34 类映射
  analysis/
    __init__.py               基础分析查询的公共导出
    basic.py                  向听/等待/弃牌牌效/条件打点的 Metric 包装
    efficiency.py             牌形计算、可见库存提取
    minimal_policy.py         最小向听、最大未见进张的偏好排序
    fixed_policy.py           人工参数牌效、条件打点及攻防启发式
    routes.py                 路线距离、专用改善牌和速度代价
    action_policy.py          动作假设分支及统一排序
    dispatcher.py             已实现方法与任务的同步调度
    output.py                 统一摘要、版本检查与调试 JSON（不重算）
  rules/
    __init__.py
    config.py                 无状态规则配置（基础结构可依赖，不导入模型）
    discards.py               模拟与分析共享的弃牌限制
    scoring.py                条件番符、组形/等待、振听条件组合
    call_rules.py             已成立吃碰的食替集合
  processing/
    events.py                 有限人工事件的载荷校验
    progression.py            摸打/响应确认/暂停的快照转换
    records.py                记录、版本、撤销、保存加载与回放
    analysis_input.py         原观察者前缀提取与 AnalysisRequest 构造
    observed_simulation.py    应用已确认的可见模拟事件变化
  observation/
    observer.py               可复现缺失观察、入口事实与无泄漏历史
    dataset.py                逐行数据变体、来源分区、分片与有限分析审计
    __main__.py               批量命令
  adapters/
    tenhou.py                 来源事实转四家可见 RoundRecord；样本输入/标签分离
    mjai.py                   完整四人 MJAI、压缩文件与 tar 导入；观察者独立牌 ID
    __main__.py               本地 XML/gzip 导出命令
  training/
    ranking.py                可见特征缓存、来源分区、离线评测与参数拟合
    __main__.py               prepare / evaluate / fit 命令
  simulation/
    engine.py                 全知单局推进与动作裁决
    settlement.py             和牌/流局支付及下一局场况
    observation.py            全知事实到玩家可见记录
    persistence.py            全知初始状态＋动作的保存重放
test_ui/
  app.py                      当前单局面窗口与交互
  positions.py                测试快照、随机发牌与手动编辑
  tiles.py                    图形牌面及可选 PNG 读取
  smoke.py                    独立桌面交互检查
  __main__.py                 python -m test_ui 入口
event_ui/                    独立事件录入、图形牌河与只读回放窗口
  analysis_panel.py           方法/任务选择、摘要与实际输入输出调试区
simulator_ui/                新独立对局 UI；不改动原两个工具
assets/mahjong/               可选牌面素材与命名说明
tests/                       核心自动化测试
examples/                    无界面示例与保留的历史 JSON
docs/                        设计、接口、功能说明
run_position_ui.py           IDE 启动入口
run_event_ui.py              新事件工具的 IDE 启动入口
```

基础数据目前只有三个职责明确的文件，继续放在包根部，不再增加一层转发。用户要求原 `test_ui` 保持不变，因此推进工具另建 `event_ui`，只读取复用牌面组件。神经网络和优化策略没有空实现或提前建立的框架。

## 依赖方向

```text
test_ui / examples
       ↓
analysis → rules
       ↓      ↓
contracts / models / tiles

processing → contracts、models、tiles（后续共用 rules 查询）
event_ui → processing、analysis、牌面组件
simulation → rules、models、contracts（不依赖 analysis/UI）
simulator_ui → simulation、processing、analysis、牌面组件
```

- `tiles` 不依赖规则、分析、处理或 UI；`models` 依赖牌码工具及无模型依赖的 rules/config 配置；`contracts` 依赖基础模型。
- `rules` 是共享纯计算，不依赖分析策略、UI 或未来推进器，不写入牌局状态。
- `analysis` 使用基础事实和规则计算，不依赖 UI 或推进器，不维护真实牌山/第二套牌局。
- `test_ui` 构造/展示输入并调用核心。当前随机发牌只服务测试，不能变成核心推进所依赖的发牌器。
- 处理模块推进后，通过标准请求将同版本事实交给分析；分析结果不反向改写记录。二者共用规则查询，不相互导入对方实现。

分析指标可以是 exact，最小排序仍是 heuristic；整理目录不改变 UNKNOWN/None、coverage/completeness、可执行推荐仅引用 allowed 等既有契约。

## 公共调用与迁移

```python
from nanikiru.analysis import analyze_shanten, analyze_waits, analyze_discard_shapes, score_hand
from nanikiru.analysis.efficiency import calculate_shanten
from nanikiru.analysis.minimal_policy import rank_discard_candidates
from nanikiru.rules.scoring import evaluate
from nanikiru.rules.call_rules import call_banned
```

`analysis/__init__.py` 只导出公共查询，不承载算法，也不是另一套实现。具体纯计算及策略按子模块导入。当前 analyze 调度器已实现，处理与分析仍不相互导入实现；由事件 UI 或示例串接。详见[分析调用链](analysis-pipeline.md)。

| 原位置 | 新位置 |
| --- | --- |
| nanikiru/analysis.py | nanikiru/analysis/basic.py，由 analysis 包导出原查询 |
| nanikiru/efficiency.py | nanikiru/analysis/efficiency.py |
| nanikiru/minimal_policy.py | nanikiru/analysis/minimal_policy.py |
| nanikiru/scoring.py | nanikiru/rules/scoring.py |
| nanikiru/call_rules.py | nanikiru/rules/call_rules.py |

旧的四个顶层工具导入路径不再保留转发文件；仓库测试、示例和 UI 已迁移。根部数据导入与 UI 启动命令不变。包发现配置继续使用 `nanikiru*`，包含新子包。

## 人工事件推进的归属

现已建立 `nanikiru/processing/`，前三级目录功能已实现；完整规则查询待后续：

| 计划位置 | 职责 |
| --- | --- |
| processing/events.py | 摸牌、弃牌、确认响应结束的明确载荷与校验 |
| processing/progression.py | 将已确认事件应用到快照，更新牌位置、数量和阶段 |
| processing/records.py | 初始快照＋事件记录、版本、撤销、保存加载和回放 |
| rules/ 下相应文件 | 共用的动作/阶段约束查询；不实现策略排名 |
| event_ui/ | 人工输入、阶段/牌河/历史展示；调用处理接口，不自写状态推进；原 test_ui 不变 |

最小范围仍是摸牌、弃牌、明确确认无鸣牌/无和牌后继续，及保存回放/撤销。中途接入允许明确未知；对手私有摸牌不进入可见事件。人工事件工具仍不接收吃碰杠、立直和结算，不得静默跳过；新的模拟器已实现这些动作，并通过独立 sim_* 可见事件投影接入记录和分析，见[模拟器说明](simulator.md)。

以后新增优化牌效策略或神经网络推理，可在 `analysis/` 内按算法新增文件；训练数据转换、训练脚本在真正开发时独立归置，不让实时分析依赖训练运行环境。识别输入适配与处理纠错也按届时实际边界拆分，不提前搭建服务或插件系统。

## 架构调整阶段验收

移动实现并调整导入，不修改算法、数据格式、UI 行为或依赖版本。迁移前后均为 41 项测试通过；迁移后两个无界面示例、桌面 smoke、核心包导入、旧路径移除、依赖方向与编译检查通过。结果在 README 同步。

后续推进阶段为 58 项单元测试及独立事件 UI 自动交互检查；能力与限制见[推进说明](manual-progression.md)。

规则增量：rules/config.py 提供天凤半庄预设，rules/match.py 提供纯整场结束判断；模拟器结算引用它，分析请求携带同一规则配置。训练入口校验来源规则，详见 tenhou-rules.md。
