# 算分依赖

- 包：`mahjong==2.0.0`，MIT 许可。
- 发布页：https://pypi.org/project/mahjong/2.0.0/
- 上游：https://github.com/MahjongRepository/mahjong
- 许可：https://github.com/MahjongRepository/mahjong/blob/master/LICENSE.txt
- API 文档：https://mahjongrepository.github.io/mahjong/modules/hand_calculating/hand.html

本项目通过安装依赖并调用公开 API 使用，没有复制或改编上游源码。发布包含该依赖的发行包时，应保留其随包许可证和版权说明。

采用原因：普通形拆分、特殊形、役种、番符及宝牌计数范围较大，直接使用固定版本并封装比本轮重新实现整套算分更容易核验。`nanikiru/scoring.py` 转换本地 Tile/Meld、风牌和和牌上下文；`Game` 决定一发、海底、杠开宝、合法和牌时机与最终支付。库不替代环境规则。

已在 Python 3.11.4 虚拟环境中安装及运行本地测试。使用已知点数案例验证赤宝/普通宝牌、七对子、国士、场风、无役、天和与包牌等，不把上游报告的测试量当作本项目的测试量。

基础牌效模块 `nanikiru/efficiency.py` 另调用同版本 `Shanten.calculate_shanten`，按有无副露控制七对子、国士计算；有效进张、可见牌库存和候选排序由本地实现，没有新增依赖或复制上游源码。
