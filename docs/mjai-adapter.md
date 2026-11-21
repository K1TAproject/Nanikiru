# MJAI 牌谱适配器

## 已实现

`nanikiru/adapters/mjai.py` 将完整四人赤牌 MJAI 牌谱转换为四个玩家各自可见的 RoundRecord，并输出现有训练流水线接受的 `discard-sample-v1`。支持 JSONL、gzip、Zstandard，以及逐场读取 tar；不会将 tar 成员路径解压到文件系统。

支持摸打、吃、碰、明杠、暗杠、加杠、抢杠、立直声明/成立、宝牌增加、自摸、多家荣和和流局。共用既有来源事件投影逻辑，不调用模拟器重裁原谱的和牌或点数。训练导出目前只生成弃牌/立直决策标签，读取鸣牌事件不等于已经生成鸣牌训练样本。

输入要求完整初始暗手、明确摸牌、手切/摸切和终局点数变化。三人、无赤、`?` 隐藏暗手网络日志、截断文件及不一致状态明确报错。要模拟中途接入或观察缺失，应在完整导入后通过 observation 层生成；不能把缺失输入填成事实。

MJAI 没有实体牌编号。本地分配编号只用于关联状态，输出改为各观察者独立的无语义编号，避免编号透露他家暗手。其他玩家摸牌隐藏，未来和牌/收益仅进入独立 labels。未明确提供的流局原因、里宝等保留 UNKNOWN；源文件没有记录的过和行为不据此证明无振听。

## 数据集格式核查

来源：<https://huggingface.co/datasets/nikketryhard/tenhou-houou-mjai>

核查固定版本 `6450bb584cef43ede3b514df48e301a832c6818b`。仓库说明将天凤年度包、雀魂玉之间和王座年度包列为 MJAI 转换数据；`tenhou-original-xml.tar.zst` 是存有原始 XML 的 SQLite 数据库归档，不能交给本适配器。

实际检查：本地 2026 天凤包含 12,188 个 `.mjai.json.zst` 文件；前 40 场成功解析。另以远程范围读取抽查 2009 天凤、2019 雀魂王座各一场，均为相同 JSON 行事件结构且解析成功。此结论不表示逐一检验了全部年度、全部牌谱或对局质量；2009 样本事件较少样化，仍应经过后续质量筛选。

转换器说明：<https://raw.githubusercontent.com/NikkeTryHard/tenhou-to-mjai/main/README.md>

## 使用

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m nanikiru.adapters.mjai data/external-replays/huggingface/tenhou-houou-2026.tar data/external-replays/mjai-example --profile tenhou --limit 10 --samples-per-game 12
```

输出目录必须尚不存在。`--limit` 为读取场数，`--offset` 跳过前若干场；`--samples-per-game` 在每场决策序列中确定性均匀抽样，不提供则导出全部弃牌决策。`--records` 另外保存每局四个视角记录，体积会更大。逐场解析、逐行导出，不同时加载整个年度包；失败时报告保持 incomplete，不应将其当作完整训练语料。

输出 `discard-samples.jsonl` 和 `import-report.json`，后者记录源文件位置、每场内容 SHA256、成员名、规则、选择参数和计数。同一导入中按内容去重，训练层继续按来源分组划分数据，避免同场局面落入不同集合。

```powershell
.\.venv\Scripts\python.exe -m nanikiru.training prepare data/external-replays/mjai-example/discard-samples.jsonl --output data/external-replays/mjai-cache
.\.venv\Scripts\python.exe -m nanikiru.training evaluate data/external-replays/mjai-cache --output data/external-replays/mjai-evaluation
```

MJAI 并不完整声明平台规则，必须显式传规则。`--profile tenhou` 使用现有天凤训练规则配置（南场、特殊双倍役满关闭），不自动识别所有房间变体；雀魂或特殊房间请使用 `--profile custom --rules path/to/rules.json`，JSON 字段对应 RuleConfig。原谱结算保持来源事实，条件打点分析采用所选规则，两者不可混同。

程序接口：`parse_mjai(data, rules=...)`、`load_mjai(path, rules=...)`、`iter_archive(path, limit=..., offset=...)`、`export_mjai(...)`。依赖固定为 `zstandard==0.25.0`。现有天凤 XML 入口与 UI 不变。

## 本次验证（2026-11-13）

全套 144 项测试通过（本适配新增 7 项）；覆盖格式拒绝、可见性和编号隔离、吃碰各类杠/抢杠、立直、多家荣和、压缩/tar、安全路径、保存回放、缺失观察和训练缓存接入。

真实小批试导入位置：`data/external-replays/mjai-2026-pilot/`。固定取 2026 包前 10 场，共 110 局、5,235 个决策，每场抽 12 条，共 120 条。特征缓存 `data/external-replays/mjai-2026-cache/` 成功接收全部 120 条（训练 108，验证 12，按来源哈希划分；这一小批没有测试集来源，不用于独立测试强度结论）。没有进行本次权重拟合。

2026-11-13 规则检查增量：--profile tenhou 已替换为经核查的平台预设，并要求 2023+ 的 00a9/0000 来源标记与标准开局。此前的小批仅为接入试验；正式规则数据须重新导入。详见 [天凤规则核查](tenhou-rules.md)。

2026-11-14：补充四家立直流局时省略第四次 reach_accepted 的有限规范化，记录推导依据、不回写早期输入，并继续检查下一局点数。条件与验证见 [训练进度说明](training-monitor.md)。

同日进一步加固事件阶段、供托与点数衔接、输入类型和导出错误定位；检查范围及复跑方式见 [适配器事件检查](adapter-audit.md)。
