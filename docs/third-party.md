# 第三方依赖

- 包：`mahjong==2.0.0`，MIT 许可。
- [发布页](https://pypi.org/project/mahjong/2.0.0/)
- [上游](https://github.com/MahjongRepository/mahjong)
- [许可](https://github.com/MahjongRepository/mahjong/blob/master/LICENSE.txt)

本项目安装依赖并调用 API，没有复制上游源码。发布包含该依赖的发行包时保留随包许可证与版权说明。

当前 rules/scoring.py 将牌码与临时组关系转换为库输入，计算条件番符与基础支付；analysis/efficiency.py 调用 Shanten。旧 Game 已移除，模拟规则实现边界见 simulator.md。

已用本地固定案例检查普通形、赤宝/指示牌、七对子、国士、明暗杠及无役等；这不是所有规则/拆分的穷举证明。具体测试以当前 tests 为准，不引用旧整局测试数量。

MJAI 解压依赖 `zstandard==0.25.0`，安装包元数据声明 BSD-3-Clause；[上游](https://github.com/indygreg/python-zstandard)。仅用于读取压缩牌谱，发行时保留其随包许可证。
