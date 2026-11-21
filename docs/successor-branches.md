# 合法候选与条件后继分支

2026-11-17 实现。目标仍为严格最终平均顺位优先，同顺位价值时精算点次之。A 规已有精算点结算，本轮沿用；终局接口也保留最终得点，未将它们与顺位加权相加。

## 分清两个接口

1. `rules.visible_actions.additional_actions(request)`：只读玩家可见输入。新增九种九牌，完整历史确认首巡未被打断时才 allowed，历史不足为 unknown。既有弃牌计算与额外动作合起来覆盖 Action 的各类候选。不能从未知对手手牌生成确定的他人许可。
2. `arena.branches.successors(game, action)`：离线模拟环境的条件后继生成器。复制输入，通过同一 Simulator.submit 执行；枚举尚未提交者的全部响应组合，直到响应解决后的下一个决策点或本局结束。它不是接收 AnalysisRequest 的在线搜索器。

分支复制不改变原局；非法动作由原规则校验拒绝。枚举器没有强制和牌、强制过牌或只保留最高旧启发分分支；必须遍历完才能声称当前响应窗枚举完整。响应组合是各家的条件行为，不是主家可控制的选择，也不默认等概率。

## 覆盖的推进

| 根动作 | 实际后继处理 |
| --- | --- |
| 弃牌／立直 | 所有响应组合；优先级、多家荣和／三家流局、立直成立扣棒、自动流局、下一家摸牌 |
| 吃／碰 | 保留输给竞争响应的分支；成立后暴露全部合法弃牌，按食替过滤 |
| 过 | 通过真实引擎更新过和事实与临时／立直振听，继续解决其他响应 |
| 明／暗／加杠 | 各自合法响应；抢杠不完成杠，不补牌；成立后补牌、按规则即时／延迟翻宝，保留下一步和牌、弃牌或再杠选择 |
| 荣和／自摸 | 真实支付、里宝、供托、连庄、局末／半庄末；不自摸是同决策点的弃牌等分支 |
| 九种九牌 | 宣告后真实流局结算与下一局场况；继续打是同一状态的其他候选 |
| 四风连打／四家立直／四杠等 | 由相应动作推进自动触发，不增加伪造的独立流局动作 |

每个 `Successor` 保留 `actions`（根动作及响应序列）与隔离的环境 `game`；`decisions()` 返回下一决策点全部合法选择。后续再次调用 successors 即可继续展开；局末使用既有 result.next_round 交接，不将局末当作半庄末。不会自动把另一家的下一步选成对本家最有利的动作。

## 可见信息与随机分支边界

**game 是一个给定的完整模拟世界。其摸牌、新宝牌、里宝在该世界中已确定；枚举响应不等于枚举所有未知牌世界。** 本接口仅用于离线规则展开、模拟采样的环境推进和测试。不能把真实进行中牌局的 game 直接分叉后，按真实牌山的后果选择线上动作。

需要调用机器人时，使用 `Successor.observer_turn(seat)`，复用 Session 的当事人可见请求和无语义牌 ID；不将 Successor.game、动作组合中他人私有响应或终局标签传给机器人。后续规划仍需从当前可见信息生成一致的假设世界、权重／对手模型，并按信息集聚合；本轮不实现该采样器或期望价值预测。任何采样都不能允许策略在尚未观察补牌前依据该牌选择是否杠。

## 分析结果与终局目标

v2 的吃碰结果现保留 `action_evaluation.value.follow_up_candidates` 全部合法鸣后弃牌及本次评分；换参数时须重新计算，不能把旧的 follow_up_discard 当永久最优分支。该补充没有完成响应训练缓存导出。

九种九牌候选的 policy_score 为 unavailable，原因是尚无局末继续价值；不能用随意常数与旧启发分混排。`situation.action_value_coverage` 显式报告未评分候选。现有 fixed-v2 推荐仍是旧启发策略，不是全部候选均已估值的顺位策略；本轮未删除其强制和牌排序，也未把离线全知分支接入它。

`Successor.terminal_value(seat)` 仅在引擎确认半庄结束后输出条件精确标签：`rank_value=-最终名次`、`secondary_value`、单位和 `final_points`。A 规优先使用现有 result_points；无该字段时回退最终得点。未终局返回 unavailable，绝不以当前点数排名替代最终顺位。该值在给定世界中精确，不能冒充可见输入下已知的期望值。

## 使用示例（离线环境）

```python
from nanikiru.simulation import Simulator
from nanikiru.rules.config import tenhou_hanchan
from nanikiru.arena.branches import successors

game = Simulator(rules=tenhou_hanchan(), seed=18)
action = game.legal_actions(game.actor)[0]
for branch in successors(game, action):
    print(branch.actions, branch.decisions(), branch.game.result)
    # 这些是环境分支，不能由本家贪心挑选他人的响应。
```

测试见 `tests/test_successor_branches.py`；覆盖多家响应、过和振听、三种杠、鸣牌竞争与鸣后选择、主动流局及自动流局、终局标签、输入不变和可见候选与引擎的一致性。原有模拟器规则测试仍负责更广的具体支付和时序组合。

最终回归：`python -m unittest discover -s tests -v`，201 项通过（139.137 秒），其中新增专项 12 项。日志 `data/external-bots/branch-tests-final-20261117.log`。未改 UI，未进行桌面检查或正式训练。
