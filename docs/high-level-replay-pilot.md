# 高水平真实牌谱接入试验

日期：2026-11-13。本次完成公开来源调查、小批真实牌谱下载、质量筛选、玩家视角提取与固定样本评测接入；不等同于已建立代表性训练集或取得通用训练授权。

## 来源与核实

最终采用 [houou-statistics 的 data 示例目录](https://github.com/chienshyong/houou-statistics/tree/80dc535dc7eab1a0faf18a2fbcfe72db2067976a/data)，固定到提交 `80dc535dc7eab1a0faf18a2fbcfe72db2067976a`。三份文件都包含 `GO type=169, lobby=0`、四家段位信息及 `owari` 整场结束记录：

| 文件 | 玩家段位 | 局数 | 弃牌／立直决策 |
| --- | --- | ---: | ---: |
| examplelog.xml | 七、七、八、七段 | 13 | 556 |
| examplelog2.xml | 七、七、七、九段 | 12 | 522 |
| examplelog3.xml | 八、七、七、七段 | 3 | 151 |
| 合计 | 四人有赤、食断、凤凰南场 | 28 | 1229 |

这是仓库挑选的示例牌谱，不是随机抽取的普通对局总体，不能据此评价真实整体强度。排名是来源记录中的段位，不是本项目重新认证的玩家水平。

另外检查了 tenhou-python-bot 的 1.txt—4.txt：前两份是私人房，后两份是较低桌型且内容重复，均未进入本批高段位输入。此前文档中的 30 局兼容检查是包含重复来源的格式检查，不能当成 30 局独立高水平数据。

天凤官方当前索引 `https://tenhou.net/sc/raw/list.cgi` 的本次请求返回空内容，未把它当作成功采集，也未反复轮询。另找到 [Tenhou／雀魂 MJAI 数据集](https://huggingface.co/datasets/nikketryhard/tenhou-houou-mjai)，但其格式不直接适配当前 XML 入口、数据量较大，且来源授权须单独核实；本轮未下载该数据集。

## 已接入的本地数据

所有原始牌谱和派生内容保存在被 Git 忽略的 `data/external-replays/`，不作为代码仓库内容再分发：

- `probe-20261113/sources.json`：固定下载地址、原文 SHA-256、桌型和段位编码。
- `houou-pilot/import-report.json`：逐文件接收／拒绝原因及计数。
- `houou-pilot/<source_sha256>/hand-*-seat-*.json`：28 局的四家玩家可见记录，共 112 份。
- `houou-pilot/discard-samples.jsonl`：1229 个完整导出样本，标签与输入分离。文件含事件前缀，因此体积约 132 MB。
- `houou-pilot/audit-samples.jsonl`：每场按决策序号等距抽取 24 条，共 72 条。抽样不看胜负、役种或推荐结果。
- `houou-pilot-features-current/`：对导出完成后的固定 72 条输入生成的有效特征缓存。

旧目录 `houou-pilot-features/` 属于前一次缓存，不应用于本次评测；以带 `-current` 的目录和其 manifest 为准。缓存检查现会拒绝提取过程中发生内容变化的输入，避免边导出边提取形成无法复现的结果。

分析输入不包含其他玩家暗手、牌山种子、未来行动或终局标签。桌型和段位只用于来源筛选与导入报告，不进入策略特征。

## 可复用入口

```powershell
# 仅接收本地文件；不执行网络抓取。输出必须为新目录。
.\.venv\Scripts\python.exe -m nanikiru.adapters.batch game1.xml game2.xml --output data/external-replays/new-import --min-dan 7 --audit-per-game 24

# 必须等待导入完成后再执行下一步。
.\.venv\Scripts\python.exe -m nanikiru.training prepare data/external-replays/new-import/audit-samples.jsonl --output data/external-replays/new-features --seed 0
.\.venv\Scripts\python.exe -m nanikiru.training evaluate data/external-replays/new-features --output data/external-replays/new-evaluation.json
```

批量入口严格筛选四人凤凰桌的已支持有赤食断模式、公共房、最低段位与整场结束标记；精确重复内容去重。解析失败单独记录，既不补造未知事件，也不把拒绝文件算作成功样本。旧单文件适配器仍可处理其他已支持桌型。

只有 3 个独立来源；按既定种子 0 划分后为训练 2 场、测试 1 场，没有验证来源。本次不换种子凑分区、不拆同一场进入不同集合，也不运行缺少验证集的权重拟合。当前工作验证数据通路，不能产生可信的优化权重。

## 实际评测结果

报告为 `data/external-replays/houou-pilot-evaluation.json`。使用当前默认 v2 权重，重复评测输出完全一致，来源校验和匹配。

| 分区 | 抽样数 | 可评价数 | v2 同分允许首选命中率 | v1 首选命中率 |
| --- | ---: | ---: | ---: | ---: |
| train（本次未训练） | 48 | 46 | 67.39% | 73.91% |
| validation | 0 | 0 | 不可计算 | 不可计算 |
| test | 24 | 24 | 50.00% | 83.33% |

训练分区另有 2 个强制动作排除在正确率分母外。本次抽样的可评价标签均为普通弃牌，因此上述 v1 对照分母一致。v2 测试同分均分命中率为 43.06%，排序损失为 1.6033。允许同分命中会比唯一选择口径更宽松，应同时看报告中的同分均分指标。

这些数字衡量与记录动作的一致程度，不是和牌率、正确决策概率或实战强度。只有一场测试来源，不能据此得出稳定强弱结论，也不能据测试结果直接调权重。本次没有替换默认参数。

新增桌型／段位／终局筛选、重复与拒绝处理、可见导出及输入提取期间变化检测测试；全套 **137 项测试通过**。本轮没有修改 UI。

## 使用范围

[天凤官方说明](https://tenhou.net/sc/raw/)限制牌谱及索引再分发，并对一般麻将用途要求联系官方。公开仓库可访问不代表其能授予上游牌谱的一切训练权利。本轮数据仅作本地小样本接入研究，未发布牌谱或训练权重；在扩大为本项目正式训练语料前，需确认适用授权。本次没有代用户联系任何人。
