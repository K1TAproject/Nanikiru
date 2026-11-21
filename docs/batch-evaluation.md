# A 规批量半庄评测

2026-11-15。本工具进行策略评测，不训练或调整分析器参数。使用现有 `tenhou_hanchan()` A 规引擎，连续推进完整半庄，包括本场／供托、连庄、击飞、西入及终场。原 UI 均不修改。

## 启动与进度

本机已准备 akochan 源码与编译文件。运行：

```powershell
.\.venv\Scripts\python.exe -m nanikiru.arena examples/evaluation-akochan.json --output data/evaluations/my-akochan-run
```

示例是 **拟合 v2 对三家 akochan**，一个种子轮换四个座位，共四场完整半庄。这是小规模接入检查，不能证明强弱。修改配置的 `seeds` 可扩大批量；一项种子始终对应四场，不随机拆掉部分座位。可复制配置创建新的实验，但其中相对路径相对于配置文件所在目录。

每个动作开始前写入真实进度，控制台约每五秒输出一次。另一个终端可直接查看：

```powershell
Get-Content data/evaluations/my-akochan-run/status.json
Get-Content data/evaluations/my-akochan-run/summary.json
```

更推荐同时核对进程锁的进度命令，避免进程意外退出后把旧 `running` 文件当成仍在计算：

```powershell
.\.venv\Scripts\python.exe -m examples.evaluation_status data/evaluations/my-akochan-run --watch
```

`process_active` 表示是否有评测进程持有目录锁。文件记录 running 但锁已释放时，显示 `interrupted_without_final_status`，不会伪装正在运行。这个命令只查看，不重启或终止任务。

`status.json` 包含正在计算的机器人、种子、座位轮换、局序号、操作序号及已完成场数；最后一次操作开始时间停留不变，表示当前动作仍在计算或进程停止，不代表已完成。外部动作有超时上限。`elapsed_seconds` 是本次运行进程耗时，续跑时重新计时。

Ctrl+C 可中断。**执行完全相同的命令即可续跑**：完成的半庄跳过并核对回放哈希，未完成的半庄从头重跑。不是从某一步恢复第三方机器人的内部状态。

未完成场次的旧回放可能仍在目录内，不能通过统计 `hand-*.json` 文件数判断当前进度。有效完成场次以 `result.json` 及其 `hands` 清单为准，实时进度以进程锁和 `status.json` 为准。

配置、分析代码、外部二进制／参数或依赖版本改变后必须换输出目录，不允许把不同版本成绩混在一起。同一输出目录有进程锁，拒绝并发写入。

## 输出与统计

- `manifest.json`：A 规全部字段、种子、对手配置、版本、代码和模型／外部文件指纹。
- `match-XXXXX-R/result.json`：四家最终点数、顺位、被测座位、每局结果与回放指纹。
- `match-XXXXX-R/hand-NNN.json`：可由现有模拟器载入的完整私有回放；**不能送给策略当输入**。
- `failed-hand.json`、`failure.json`、`bot-N.stderr.log`：失败现场、被拒动作与外部进程错误。顶层 `failures.jsonl` 保留各次失败／中断记录，续跑不抹掉。失败立即停止，报告 incomplete，不作为四位、流局或零分。
- `summary.json`：平均顺位、最终原始点数、A 规结算分及四种顺位率。`result_points` 是引擎的返还点／顺位马结算分，**不是天凤段位点**。

只用已完成四个座位的种子组做汇总；未完成组明确计数。95% 区间使用独立种子组均值的正态近似；一个种子组时返回 null。少量种子的区间并不可靠，不能据四场试跑宣称强度。样本数和判断标准应在正式评测前确定，不能见到领先就提前停止。

牌山随机流按种子及局序号派生，不含机器人名称或版本。轮换时四家起家位置改变；鸣牌和连庄可能让同种子对局路径分叉，因此只能降低运气差异，不能消除随机性。此处“主家”指被测玩家，不是固定庄家。

新旧版本采用同样种子、规则与对手配置各跑一份，再比较：

```powershell
.\.venv\Scripts\python.exe -m nanikiru.arena data/evaluations/old --compare data/evaluations/new --output data/evaluations/comparison.json
```

比较报告给出新减旧的配对差值及种子组区间：顺位差为负较好，点数差为正较好。只接受两份 complete 报告，且种子计划和对手文件版本一致；会注明代码是否变化。引擎本身若改变，需另做规则回归检查，不能直接归因于策略。

## 多个外部基准

配置的 `bots` 是名称到机器人配置的映射，`candidate` 指定被测对象，`opponents` 指定三家，可混合不同名字或版本。已经实现三种接入：

| kind | 实现 |
| --- | --- |
| builtin | 现有 v1、默认 v2、已核对 A 规报告的拟合 v2 |
| akochan | 可见记录转 MJAI，由本地 C++ 桥接调用 akochan，转换鸣牌／立直等动作 |
| request-jsonl | 通用外部进程：完整可见 AnalysisRequest 输入，Action 输出 |

`request-jsonl` 不是声称所有现成 MJAI 机器人直接兼容。Mortal 等不同协议或版本仍需专用转换层和模型文件。新增基准必须显式声明 A 规、固定版本和全部行为相关文件；声明是接入要求，不代替规则边界测试。

外部配置字段与 akochan 示例相同：`command` 是参数数组，首项相对于配置文件解析，其他参数按指定 `cwd` 解释；`timeout` 是每次请求秒数，`artifacts` 应列出代码、模型和配置文件／目录。

通用请求协议，每行一个 JSON，标准输出只能用于协议，诊断写标准错误：

可运行的 Python 桥接示例为 `examples/stdio_bot.py`（通过 `python -u -m examples.stdio_bot` 启动）。它复用本项目分析器，仅验证接入方法，不作为独立强度基准。

```text
输入：{"protocol":"nanikiru-bot-v1","request":{...实际 AnalysisRequest...}}
输出：{"state_version":"与输入一致","action":{"kind":"discard","actor":0,"tile_ids":["v2"],"target_id":null}}
```

UNKNOWN 沿用 `{"$unknown":true}`。实体 ID 为当前请求的临时别名，不能跨请求缓存动作 ID；应使用完整请求重建局面。进程每场创建一次，每次只接收本家可见请求。版本、玩家、目标事件和实际动作合法性由宿主复核；拒绝时不自动换成其他动作。所有动作遵循 A 规引擎，基准无法执行的规则差异会显式报错。

本地子进程隔离不是操作系统安全沙箱；只运行可信机器人。宿主不会通过协议发送真实牌山、对手暗手或种子，但同用户权限的恶意进程理论上可以访问本机文件。

## akochan 构建与范围

来源：<https://github.com/critter-mj/akochan>，固定提交 `53188a0b926fbab38177f88c3cd87d554cf412af`。源码、Boost 及编译产物均位于 Git 忽略的 `data/external-bots/`，不随项目再分发。

本机使用已安装 MSVC、Boost 1.70.0 头文件、`NPROCS=4`（上游 Windows 默认值），通过独立桥接调用上游 `ai()`。使用原 `setup_mjai.json` 策略配置，未调整其策略参数。其顺位效用配置保留上游值，并不冒充我们的 `result_points` 或天凤段位点。早期试跑曾使用 `NPROCS=1`，已因工作区容量故障修正；不能将两个构建的成绩混入同一基准版本。

构建命令：

```powershell
.\.venv\Scripts\python.exe tools/build_akochan.py
```

脚本修复上游跨编译单元中两个可变全局变量被声明 const 的 MSVC 链接不一致，以及 `cut_tatu` 在边界判断前读取 `hai+1`／`hai+2` 的越界问题；不改策略公式。Boost 官方来源为 `https://archives.boost.io/release/1.70.0/source/boost_1_70_0.zip`，头文件目录为 `data/external-bots/boost_1_70_0/boost`。其他机器须先准备这两个依赖或指定脚本参数。`--asan` 创建独立诊断构建；运行它需将 MSVC 的 AddressSanitizer 运行库目录加入该进程的 PATH。

桥接每次只提交观察者初始暗手、公开历史及本家摸牌；他家摸牌为 `?`。立直声明／成立分开，杠宝按实际揭示时序输出；吃碰后使用 akochan 原决策序列中的后续弃牌，不重新用我们的分析器代替它。加杠声明可供抢杠响应，声明与完成不重复累计。

这是 **akochan 在本项目 A 规引擎中的表现**，不是已获得平台认证的成绩，也不声称已穷举它对所有 A 规边界的策略兼容性。运行过程一旦遇到边界不兼容就保存现场并停止。

第三方使用遵循本地源码中的 `LICENSE`；本次用于私有研究，未发布其修改源码或二进制。输出应注明 akochan 来源。

## 2026-11-16 验证记录

- 全套 184 项单元测试通过；报告日志为 `data/external-bots/evaluation-tests-verified.log`。
- `data/evaluations/v1-batch-check-02` 已完成四场完整半庄及四座位轮换；再次执行同一命令成功复用结果，没有重复对局。四家都是同一 v1，因此均顺 2.5 和均分 25000 是对称性检查，不是实力结论。
- 实际运行配对比较命令，报告 `data/evaluations/v1-self-comparison.json`；同一批次与自身比较的三项差值均为零。
- 通用外部进程有效动作、过期回复、超时；akochan 可见性、吃后弃牌、立直声明／成立、暗杠、加杠声明去重及延迟杠宝均已检查。
- Windows 短暂读锁曾导致第一版进度写入失败，已增加有限重试并通过复现测试。旧失败目录保留，新检查未混入旧结果。
- 实际 akochan 小批次位于 `data/evaluations/akochan-pilot-02`。该批是否完成以 `status.json` 和进程锁检查为准；不能把已完成若干单局当成完成四场半庄。
- 2026-11-16 已确认该批第一个座位的完整半庄结束，共 10 局，结果位于 `match-00000-0/result.json`；第二座位随后因下述原生故障停止，批次未完成。这只是接入验收样本，不据单场四位或点数判断模型强弱。
- 本轮不改分析器参数、规则引擎或现有 UI；未重新进行 UI 检查。

## 外部进程故障修复

旧 `akochan-pilot-02` 第二个座位第 9 局第 9 个操作，原生进程退出码为 `0xC0000409`，并非超长响应。内存诊断发现 `cut_tatu` 的越界读取；进一步原生调用栈定位到 `Tehai_Group::set_tav_init` 的每线程 100000 项固定工作区溢出并抛出 `bad_alloc`。旧构建强制单线程，同时缩小了总工作区容量。修复恢复上游 Windows 的四线程构建并调整边界检查顺序，不通过删减候选或替换动作继续运行。

固定可见输入保存在 `tests/fixtures/akochan-capacity.json`，本机安装正式 akochan 后会运行原生回归测试。独立 ASan 构建已在此输入上正常退出并返回弃牌，没有内存诊断错误。传输层现在区分 EOF、截断、过长和超时，EOF 会记录进程退出码；桥接对可捕获的 C++ 异常写标准错误。

从保存的失败局面继续验证：

```powershell
.\.venv\Scripts\python.exe -m examples.recheck_akochan_failure --output data/evaluations/akochan-failure-recheck
```

该脚本仅适用于所记录的第二个座位失败样本（被测 v2 在 1 家），结果标记为故障回归，不能充当新的完整半庄统计样本。正式评测需使用新输出目录，旧代码／外部文件指纹检查不得绕过。

正式四线程版本已通过原生失败输入测试。`data/evaluations/akochan-failure-recheck-01` 从原失败点继续 78 个操作后正常荒牌流局并结束半庄；保存的 87 步回放已重新加载，结算一致且点数守恒。此结果只证明已知故障回归通过，不保证所有局面都不会触及上游固定容量限制。旧批次保留，不混用新旧构建的成绩。

修复后全套 **185 项测试通过**（130.797 秒），包括本机实际 akochan 原生回归；日志 `data/external-bots/evaluation-tests-fixed.log`。本次未重跑完整四座位外部批次，亦未修改分析器权重、A 规或 UI。

## 修复后完整基准复跑（2026-11-16）

随后使用同一 `examples/evaluation-akochan.json` 配置（种子 18、拟合 v2 对三家 akochan，四座位轮换），在新目录 `data/evaluations/akochan-pilot-03` 重跑。约 21 分钟完成 **4/4 场完整半庄、36 局、2016 个操作**，进程退出码为 0，`status.json` 与 `summary.json` 均为 complete。没有外部崩溃、超时、非法动作或失败记录，各外部标准错误日志为空。

逐局重新加载全部回放，核对文件 SHA256、操作数、结算、点数守恒及跨局场况衔接，并重新计算汇总，全部通过。检查报告为该目录下 `verification.json`，运行日志为 `data/external-bots/akochan-pilot-03.log`。

| v2 座位 | 单局数 | 最终顺位 | 最终点数 |
| --- | --- | --- | --- |
| 0 | 12 | 4 | 14400 |
| 1 | 9 | 4 | 16900 |
| 2 | 9 | 3 | 16800 |
| 3 | 6 | 4 | -3000 |

平均顺位 3.75，平均最终点数 11275。此轮证明原基准试跑在修复后能够完整结束；只有一个种子组，不作为稳定实力估计，不据此调整参数。旧 `akochan-pilot-02` 保留为失败记录，不与新构建混合统计。本轮未修改运行代码、分析器权重、规则或 UI。
