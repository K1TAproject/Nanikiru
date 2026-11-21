# Nanikiru 项目协作说明

- 持续研发记录：开始涉及项目历史、实验或后续开发的任务时先读 `docs/research-log.md` 的当前交接状态，再按需读具体条目和报告。用户要求保存讨论、失败、修正、训练及测试指针；每次实质决策／实现／实验结束后追加记录并更新当前状态。区分确认、实测、历史记录和待验证，不覆盖失败历史，不将文档摘要代替原始证据。不需要为此自动修改全局记忆。

- 2026-11-16 用户确认严格平均最终顺位优先、实际等效时比较精算点，其余仅观察；授权 v2 参数/公式审查及新一轮训练，取代此前暂停调参的限制。见 `docs/v2-parameter-audit.md`。旧策略快照与权重保留；新版公式权重须匹配版本。牌谱拟合只是初始化，不能以一致率提升宣称顺位提升；本轮先导不自动推广。

- 2026-11-15 用户暂停参数调整，授权 A 规批量完整半庄评测及多基准接口，首选 akochan。实现见 `docs/batch-evaluation.md`；入口 `python -m nanikiru.arena`。严格按现有 A 规结算衔接下一局，外部非法／超时动作停止并保存现场，不静默替换。完整四座位种子组统计，失败不伪装正式成绩；不改原 UI。第三方源码与构建产物仅保留于被忽略的 `data/external-bots/`。

- 2026-11-14 新增 `nanikiru/arena/` 机器人可见请求／合法动作桥接与独立 `table_ui/` 四方牌桌，见 `docs/robot-table.md`。复用现有模拟器及 A 规；原 `test_ui/` 保持不变。机器人只接收当前观察者请求，实体牌 ID 转临时别名，提交须校验版本并转换局部事件目标。入口 `python -m table_ui`，桌面检查 `python -m table_ui.smoke`。本轮拟合仅覆盖弃牌／立直，鸣牌仍为启发式；单局结束不自动开下一局。

## 当前阶段

- 2026-11-10 用户已授权按新方向修改底层数据、适配必要计算、删除不适配/冗余实现、停用旧 UI。此授权取代此前“只讨论、不动代码”和无条件保留旧接口/测试的阶段限制。
- 2026-11-11 用户进一步授权基础分析、最小牌效排序与单局面 UI，后续补充随机发牌和全牌种手动组牌。当前已实现；该阶段未实现的规则推进由后述模拟器阶段补充，模型和识别仍未实现。随后授权的有限人工推进见下文，不自行扩展成完整对战引擎。
- 先读 `docs/design.md`、`docs/analysis-interface.md`、`docs/observation-data-design.md` 和 `docs/direction-adaptation.md`。规则计算需查 `docs/rules-and-settlement.md`，其中旧实现记录不代表当前能力。
- 分析函数参数和实现边界见 `docs/analysis-api-list.md`、`docs/analysis-module.md`。
- 架构调整后用户已授权人工事件推进。当前 `processing/` 实现摸打、响应确认、暂停、撤销与保存回放，见 `docs/manual-progression.md`；吃碰杠/立直和结算未实现。遵循 `docs/architecture.md` 的依赖方向。
- 用户明确要求原单局面 `test_ui/` 保持不动。新的人工事件 UI 位于 `event_ui/`，只读取复用牌面组件，不向旧 UI 注入推进功能。
- 当前已实现人工记录前缀提取、标准请求、基础/最小牌效同步调度与统一输出，见 `docs/analysis-pipeline.md`。事件 UI 展示实际输入/结果，不另算牌效；initial_context 仅接收已定义的 riichi 入口事实，非全知数据适配器。
- 用户已确认可配置四人日麻规则并授权连续单局模拟器。当前 `simulation/` 与独立 `simulator_ui/` 已实现，规则、来源与边界见 `docs/simulator.md`。不恢复旧 Game/Recorder 兼容链，不自动开下一局。
- 2026-11-12 已实现人工参数 fixed-v2 与本地天凤 XML/gzip 适配，见 `docs/fixed-v2.md`、`docs/tenhou-adapter.md`。权重不是概率；原谱结算不经模拟器重裁；训练标签与可见请求分离。
- 已实现 `observation/` 缺失观察与逐行批量分片，见 `docs/observation.md`。分析历史 `observed_*` 仅含事实，不能作为 RoundRecord 重放；入口 initial_context 仅支持四家 riichi bool/UNKNOWN。隐藏信息必须同步遮蔽历史，牌 ID 必须去除来源编码语义。
- 默认规则为 RuleConfig；特殊双倍役满默认关闭，三家荣和实际结算。完整模拟观察共用弃牌约束；人工观察不足时仍 unknown。全知存档/调试接口绝不能作为分析输入。
- 最终方向是画面获取、识别与处理、分析、何切建议；固定算法与神经网络共用可见信息语义，不强制共用内部编码。
- 2026-11-13 v2 已扩展未听牌路线和动作候选，见 `docs/action-v2.md`。推荐不再仅弃牌；无牌 ID 的动作和条件许可必须保留。规范化观察缺少私有过和覆盖，不能据完整公开历史证明非振听。
- 已实现 `training/` 的本地特征缓存、评测与权重拟合，见 `docs/v2-training.md`。只拟合弃牌／立直语义候选；整份来源同区，测试不参与选参。合成示例权重不是正式默认值；修改代码后须重建缓存，不能忽略指纹检查。
- 已完成 3 场真实高段位牌谱本地接入试验，见 `docs/high-level-replay-pilot.md`。原始及派生数据放在 Git 忽略的 `data/external-replays/`；不得把公开访问当成通用训练授权。仅做固定小样本评测，未据此拟合或更新默认权重。

## 数据与接口约束

- 五种基础结构：TileRecord、PlayerInfo、RoundSnapshot、Event、RoundRecord。牌码包含赤牌身份；资格、等待等从事实计算，不另存一份可修改状态。
- 处理模块负责识别纠错、关联、一致性、历史修订和版本；分析模块处理已确认事实与明确未知，不修复原始识别。
- `UNKNOWN` 表示未知，`None` 表示无/不适用，空集合不能冒充缺失集合。集合覆盖使用 completeness；历史使用 coverage。
- 只有玩家当时可见信息能进入分析。真实牌山、种子、对手暗手和未来信息不进入策略输入；终局收益只可作为明确分离的训练标签。
- 初始快照＋事件为权威记录；processing 已支持有限人工事件的重建与新格式保存加载，不是完整规则或视觉适配器。UNKNOWN 的文件编码与旧存档不兼容。
- 结果区分 exact/heuristic/estimate 和可用/条件/不可用/不适用；不以零冒充未计算概率。可执行推荐只能引用 allowed 候选。
- 旧 Game、Recorder、UI 与机器人链已移除，不增加兼容壳来恢复双套状态。旧 JSON 样例是保留的历史数据，不擅自覆盖或删除。

## 开发原则

1. 编码前说明假设、范围、验收；影响正确性的歧义须澄清。多步骤工作给出简短计划及验证方式。
2. 简洁优先：复用必要纯计算，优先标准库；不预建网络服务、插件框架或未来配置。
3. 修改与请求对应；不顺便清理无关用户数据或环境。用户要求替换结构时不以“小范围修改”为由保留冗余兼容链。
4. 验证后再标记完成。非法输入不污染调用者数据；未知不变成假事实；条件算分不是和牌许可或最终结算。
5. 文档分清已实现、设计和待讨论项，同步实际测试数量；不沿用旧阶段通过数量证明新代码。

## 工具和验证

- 使用 `.\.venv\Scripts\python.exe`；依赖固定 `mahjong==2.0.0`，通过 scoring/efficiency 使用。
- 测试：`python -m unittest discover -s tests -v`；无界面示例：`python -m examples.inspect_position`。
- 核心无 UI 依赖。新单局面 UI 位于 `test_ui/`，入口 `python -m test_ui`，桌面验证 `python -m test_ui.smoke`；不恢复旧 Tk 界面。Serena 仅用于后台代码检索，不打开 Dashboard。
- 新模拟器：`python -m simulator_ui`，交互检查 `python -m simulator_ui.smoke`，无界面示例 `python -m examples.simulate_round`；动作扩展后的测试数量以当次运行结果为准。
- 独立事件 UI：`python -m event_ui`，交互验证 `python -m event_ui.smoke`。

- 2026-11-13 已实现完整四人赤牌 MJAI 适配，见 docs/mjai-adapter.md。必须显式选择规则；输出观察者独立无语义牌 ID，训练标签与可见请求分离。依赖 zstandard==0.25.0。

- 2026-11-13 新增 tenhou_hanchan() 平台预设与规则检查，见 docs/tenhou-rules.md；RuleConfig() 保留旧自定义默认。三家荣和、本场、抢暗杠、一发、包牌、形式听牌及半庄结束按预设区分。训练 CLI 默认要求已核对的天凤来源；实验使用 --rules-profile experimental。全套 152 项测试通过，模拟器自动交互检查通过。

- 2026-11-14 适配器增加事件阶段、杠宝、终局点数守恒及跨局供托检查，四家立直缺省成立仅按限定证据补齐；见 docs/adapter-audit.md。全套 170 项测试通过；真实批量检查结果以报告 status/counts/failures 为准，不以已完成分片代指全批通过。修改适配器后必须重建训练特征缓存。

- 2026-11-14：MJAI 支持四家立直终局省略第四次成立的有限规范化，见 docs/training-monitor.md。新增独立只读 training_ui 与运行进度回调；分片失败立即报告，禁止带失败进入拟合。全套 157 项测试通过，监视器自动界面检查通过；旧缓存不得绕过代码指纹校验。

- 2026-11-17 用户明确：本轮只实现最终收益导出与共享顺位模型，**用户审阅并确认模型设置前，不启动任何训练，包括小规模拟合**。实现与参数说明见 docs/shared-rank-value.md；3 场导出 QA 不是训练。旧 v2 默认不变。
