# 观察器对照实验

固定模拟种子，2 个来源、16 个弃牌时点，各生成 5 种观察，共 80 条样本。
标签中的弃牌来自测试推进，不是最优策略；本实验检查信息降级，不测策略强度。

| 观察类型 | 方法 | 可排序 / 样本 | 可执行推荐 | 全候选进张可用 |
| --- | --- | --- | --- | --- |
| complete | minimal-shape-v1 | 16 / 16 | 16 | 16 |
| complete | fixed-v2 | 16 / 16 | 16 | 16 |
| midgame | minimal-shape-v1 | 16 / 16 | 10 | 16 |
| midgame | fixed-v2 | 16 / 16 | 10 | 16 |
| discard_kind_unknown | minimal-shape-v1 | 16 / 16 | 16 | 16 |
| discard_kind_unknown | fixed-v2 | 16 / 16 | 16 | 16 |
| public_missing | minimal-shape-v1 | 16 / 16 | 16 | 5 |
| public_missing | fixed-v2 | 16 / 16 | 16 | 5 |
| mixed | minimal-shape-v1 | 16 / 16 | 10 | 3 |
| mixed | fixed-v2 | 16 / 16 | 10 | 3 |

## 两个方法的首选比较

| 观察类型 | 首选牌种存在交集 / 样本 |
| --- | --- |
| complete | 15 / 16 |
| midgame | 15 / 16 |
| discard_kind_unknown | 15 / 16 |
| public_missing | 15 / 16 |
| mixed | 16 / 16 |

首选不同不代表任一方法更优；只用于观察人工权重是否改变行为。
结果仅对应本次固定样本。随机遮蔽可能没有遮到某一局面的牌；具体掩码配置、种子和逐候选状态见分片及 audit.jsonl。
