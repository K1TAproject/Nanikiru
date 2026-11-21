# 路线特征、可重复评测与权重拟合

2026-11-16：严格平均顺位目标下的新公式与参数覆盖审计见 [v2 参数审查](v2-parameter-audit.md)。前瞻改为计入改善机会，听牌后去除宝牌／路线重复奖励；缓存 schema 更新，旧缓存须重建，旧拟合权重不得跨公式加载。以下为此前实现记录。

更新：2026-11-13。已实现本地 `prepare → evaluate → fit` 闭环，使用标准库 SQLite 缓存候选特征，不新增训练框架。当前是固定 v2 参数拟合，不是神经网络、强化学习或实战强度评估。原单局面 UI 不变。

## 1. 路线特征

`metrics.routes.feature_version = routes-v2`。普通形、七对子、国士分别计算自身的 `distance`、`effective_tiles`、`ukeire` 和 `ukeire_fraction`；门前立直复用普通形的改善集合，但仍须以后满足立直许可。赤牌在牌形中合并为五，未见数量按 34 种牌计数；已见弃牌不会加回未见库存。公开库存未知时，改善牌种仍可计算，数量及比例为 `None`。已耗尽的改善牌保留在理论集合，数量为零。

所有路线另有 `normal_distance` 和 `speed_loss`。后者是相对于普通形／七对子／国士中最快牌形的距离差，非预计多花几巡。断幺、役牌、对对和、混一色仍是启发式距离，其受役种约束的改善集合尚未计算，字段保持 `None`，不借用普通形进张。没有全役种分解或多步路线切换搜索。

当前路线分为：

```text
价值代理 = min(番数, 4) / (1 + 距离)^2
速度代价 = route_speed × speed_loss / (1 + 距离)
进张奖励 = route_ukeire × ukeire_fraction / (1 + 距离)
路线潜力 = max(0, 价值代理 - 速度代价 + 进张奖励)
综合路线项 = route × 各备选路线潜力的最大值
```

新增 `route_speed=1`、`route_ukeire=1`；原 `route=2`。未知进张不添加奖励，原字段仍保持未知。路线分是人工启发式先验，可以拟合，不是实际番数、期望收益或概率。摘要仍称“役种潜力参考”。一对而普通形已听牌的回归用例不再把遥远七对子置于门前路线之前；不据此宣称任意一对局面都不应考虑七对子。

## 2. 数据与命令

先用现有[天凤适配器](tenhou-adapter.md)导出 `discard-samples.jsonl`。可直接处理多个导出文件；也可先用[观察器](observation.md)生成完整、中途接入及公开信息缺失的 JSONL 分片。输入支持 `tenhou-discard-v1`、`discard-sample-v1`、`observed-discard-v1`。

```powershell
# 输入为已经导出的本地 JSONL，输出目录必须不存在。
.\.venv\Scripts\python.exe -m nanikiru.training prepare data/game1/discard-samples.jsonl data/game2/discard-samples.jsonl --output data/v2-features --seed 0

# 单独评测默认参数，无拟合。
.\.venv\Scripts\python.exe -m nanikiru.training evaluate data/v2-features --output data/v2-baseline.json

# 训练集搜索，验证集选择；结束后才报告测试集。
.\.venv\Scripts\python.exe -m nanikiru.training fit data/v2-features --output data/v2-fit --rounds 2

# 重新评测保存的参数。
.\.venv\Scripts\python.exe -m nanikiru.training evaluate data/v2-features --weights data/v2-fit/weights.json --output data/v2-evaluation.json

# 交给实际 v2 运行；模拟器的“v2 权重 JSON”也可粘贴文件内容。
.\.venv\Scripts\python.exe -m examples.analyze_v2 --parameters data/v2-fit/weights.json
```

`prepare` 生成 `features.sqlite` 与 `manifest.json`，逐行处理、一条样本一条缓存。来源 SHA-256、代码 SHA-256、Python／mahjong 版本、种子、分区数、重复数和缓存 SHA-256 被记录。出错目录保持 incomplete，不能训练；已有输出不覆盖。代码改变后缓存拒绝使用，须重新提取，不静默沿用旧特征。

整份牌谱的 `source_id` 经固定哈希按 80/10/10 分区，同一来源四家、不同局、缺失观察变体始终同区。若输入已有观察分区，必须使用当初观察导出的同一种子；冲突会报错。不能为凑测试集把同一场的行随机拆开。样本身份由来源、局、玩家、时点、变体组成；重复且内容一致时跳过，冲突时报错。标签中的终局收益不写入特征库，也不进入分析函数。

当前来源 ID 是适配器对解压 XML 的哈希；不同格式副本可能拥有不同哈希。采集阶段仍需按原始天凤 log ID 去重并保持唯一来源，不宣称已解决任意重编码副本去重。

## 3. 拟合及指标的准确含义

当前监督目标是**已决定弃牌时，在弃牌／立直切牌之间模仿实际动作**。同码普通实体牌合并为一个动作，赤五和普通五分开，立直与普通弃牌分开。不会因为某牌有三枚相同实体，就在损失函数中算三个备选。

- 可能和牌的决策、缺少目标候选、手牌不可计算等样本有明确跳过原因；仅一个语义候选的强制摸切单独计数，不虚增正确率。
- 吃碰杠、九种九牌及响应 pass 不参与本轮监督目标。缓存记录其他可用动作，但指标不能解释成完整动作推荐正确率。
- unknown 许可可参与条件排序学习，报告 `conditional` 数量；这不是可执行许可。公开库存缺失而许可已知的样本不属于该计数，仍在对应缺失变体组单独报告。
- 报告默认权重与拟合权重的 top1（允许同分命中）、同分均分 top1、倒数排名、softmax 排序损失，并按观察变体分组。softmax 仅是分类损失中的归一化，不是和牌率。v1 仅在弃牌标签子集上对照，分母另列；不能直接用它与包含立直的总体准确率比较。

拟合使用确定性的坐标搜索：每个参数尝试当前值的 0、0.5、1、2 倍和默认值，只根据训练损失选择；每轮完整搜索后用验证损失选择整组参数。初始参数也是可选结果，因此不保证一定改变参数。默认搜索 `route route_speed route_ukeire value risk`，可用 `--tune` 明确列出其他参数。`shanten` 固定，作为分数尺度的锚；`--initial` 可指定起始权重。

未进入选择过程的测试集在最后报告，不能根据测试结果反复调参再声称它仍是未见测试。训练／验证至少各需一个非强制、可评价决策；空分区不虚构指标。缺测试数据时报告为空，不伪造成功率。

缓存重新评分复用在线路线公式，同时保留立直押金、锁手代价及 `risk × (1 + late_defense × late)`，自动化测试比较过多组非默认参数与实际 v2 输出。当前只缓存本轮可精确重算的弃牌／立直分支；未来拟合鸣牌时必须保存全部后续弃牌分支，不能只拿默认权重下挑出的一个分支训练。

## 4. 可重复流程检查

```powershell
.\.venv\Scripts\python.exe -m examples.fit_v2 --output data/my-synthetic-check
```

示例生成 10 个独立合成来源：训练 6、验证 2、测试 2；每个有完整、中途接入、公开信息缺失三种观察，共 30 样本。标签来自 v1，不是人类高手。保存两次完全相同的拟合输出并断言报告一致。

本次运行在 `data/synthetic-ranking-check`。测试集（6 个观察样本）top1 拟合前后均为 0.5，排序损失从约 1.1468 变为 1.3185，反而变差。这是流程测试和小样本过拟合的示例，**不作为有效参数发布，不自动替换 v2 默认权重**。已有测试还检查拟合函数不读取测试分区、无验证集拒绝训练、冲突及篡改检测、未知库存、库存耗尽和在线／离线评分一致。

本阶段全套 134 项测试通过，模拟器 UI 自动化检查通过；原单局面 `test_ui/` 文件哈希未变化。

下一步使用具有代表性的普通对局，固定独立验证／测试集，按牌形、巡目和威胁程度检查误差，再补齐受役种约束的进张计算。真实实战收益还需独立模拟对战评测；与牌谱一致不等于最优。

## 5. 用户提供的下载教程核对

用户提供的 [CSDN 教程](https://blog.csdn.net/qq_44951699/article/details/157767395)说明了索引 ID、牌谱内容和 gzip／SQLite 存储的关系，对理解来源格式有用。本项目已有本地 XML／gzip 适配器，数据库文件不能直接当作 XML 输入。

截至本次核对，[phoenix-logs 原仓库](https://github.com/MahjongRepository/phoenix-logs)已归档，明确说明旧实现不兼容最新下载许可要求，推荐 [houou-logs](https://github.com/Apricot-S/houou-logs)。后者支持单会话、跳过已下载内容，并提示历史年度 ZIP 地址目前可能返回 404；不应把教程中的旧 URL 当作已验证可用。

[天凤官方日志说明](https://tenhou.net/sc/raw/)要求单会话、避免重复下载并按其索引刷新规则操作；禁止牌谱及索引再分发，对一般麻将用途要求联系官方。教程中的多线程提速不能直接照搬。本轮只核对来源说明，没有安装下载器或抓取训练集。

数据选择上不应以“役满牌谱”为主训练集：它按未来结果筛选，严重偏向特殊手牌。可将其作为特殊牌形单独测试集；主训练集应包含普通和牌、放铳及流局、不同巡目和攻守局面，并记录规则、来源和采样方法。

2026-11-13：prepare 命令默认检查天凤半庄规则和来源记录；旧数据须重新导入，合成实验须显式 --rules-profile experimental。单份缓存禁止混合规则。详见 [天凤规则核查](tenhou-rules.md)。

2026-11-13 已启动首轮真实牌谱实验，固定 1,000 场、802/118/80 场划分，实验方案和运行位置见 [第一轮实验](v2-round1.md)。训练是否完成以该目录 status.json 为准，默认权重未替换。
