# 第一轮 v2 权重实验

启动日期：2026-11-13。本轮为真实牌谱弃牌／立直模仿实验，不是神经网络训练或实战强度证明。默认参数保持不变。

## 固定方案

实验目录：`data/external-replays/v2-round1-20261113/`（Git 忽略）。

- 来源：已下载的天凤 2026 四人半庄凤凰段位战包，规则为 `tenhou-4p-hanchan-2026-v1`。
- 排除此前已反复检查的前 40 场；剩余牌谱按 `sha256('round1-20261113:' + member_name)` 排序，取前 1,000 场，避免只取年度包开头。
- 每场最多均匀抽取 30 个弃牌／立直决策，目标约 30,000 条。采样未参考行为标签或收益；第一轮仅完整观察。
- 来源按既有哈希规则、固定种子 `0` 划分：训练 802 场、验证 118 场、测试 80 场。source_id 为解压后整份牌谱内容哈希；同一来源四家和所有时点始终同区。
- 固定两轮坐标搜索：`route route_speed route_ukeire value risk`；向听及其他权重固定。训练集搜索，验证集选择；选择完成后才评价测试集。默认权重作为可保留方案，不保证一定发生变更。
- 4 个本地进程处理 40 个分片，每片 25 场；各分片继续使用现有规则检查和特征提取。合并时校验配置、代码指纹、来源唯一性和预先固定的分区，全部成功后才进入拟合。

## 产物与进度

- `experiment.json`：不可变实验方案、原始归档哈希、代码指纹、完整来源清单和分区。
- `splits/train.jsonl`、`validation.jsonl`、`test.jsonl`：逐场划分清单（不是特征数据）。
- `status.json`：当前阶段；`run.log`、`run.stderr.log`：运行记录；`process.json`：本次后台进程号。
- `import-*/discard-samples.jsonl`：各小批确认的可见输入和独立标签。
- `cache-*/`：各小批特征；`features/`：校验后合并的缓存。
- `fit/weights.json`、`fit/report.json`：仅拟合成功后生成，包含默认／拟合参数对比及三集合结果。
- 若失败，`status.json` 标记 failed，`error.txt` 保存原因；不能将中间产物当作成功训练结果。

后台任务不会自动修改模拟器默认权重。运行期间保持电脑开机且不休眠；不要修改 `nanikiru/` 分析代码，否则代码指纹检查会拒绝继续使用混杂版本的缓存。

## 复现

必须换一个尚不存在的输出目录；原目录保留以便审计。

```powershell
.\.venv\Scripts\python.exe -m examples.train_v2_round1 select data/external-replays/my-round1 --archive data/external-replays/huggingface/tenhou-houou-2026.tar
.\.venv\Scripts\python.exe -m examples.train_v2_round1 run data/external-replays/my-round1
```

分片合并新增回归测试覆盖分区保持、来源重复拒绝和分区不符拒绝。完成状态以实际 `status.json` 和最终报告为准；本说明不提前声明训练结果。

2026-11-14：昨晚实际完成 39 批，任务失败且未拟合；新训练监视器与四家立直缺失事件修复见 [训练进度说明](training-monitor.md)。本轮代码指纹变化，旧缓存保留但不得直接绕过校验复用。

2026-11-14 重训：用户授权后，在 `data/external-replays/v2-round1-A-retry-01/` 启动新任务。已逐项核对原始归档哈希、全部 1,000 个来源、分区及拟合设置与旧实验一致；重新导入并重建当前代码的特征缓存。结果以新目录 `status.json` 和最终报告为准，不预先视为拟合完成。

同次清理删除旧实验的 40 个 import 目录和 39 个 cache 目录，共 4,303,297,483 字节。原始年度包、旧实验方案、划分、原始分片、日志及错误记录保留；79 份导入/缓存报告保存于旧目录 `archived-reports/`，删除清单为 `cleanup-20261114.json`。上述旧缓存保留状态已由本次授权清理取代。
