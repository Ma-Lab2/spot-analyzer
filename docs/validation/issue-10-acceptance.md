# Issue #10 用户验收记录

本文件是由 `spot_analyzer.validation.run_complete_validation` 生成的验收记录模板。每次正式运行应同时保存 JSON 结果和此 Markdown 摘要；本文件不替用户作出接受规格的决定。

## 当前决策状态

- 代码实现：由测试结果单独判断。
- 计算成功：按每个 validation section 的结构化状态判断。
- 测量有效性：按分析记录中的质量状态和门控判断，不由流程成功推断。
- 验证完成：只有所有 in-scope section 均为 `passed` 时成立。
- 用户验收：待用户明确决定。
- 生产发布：不属于 Issue #10；WPF、打包和 clean-machine 发布是后续范围。
- `standard-profile-v1` / `quality-profile-v1`：保持 `profile_validation: provisional`。

## 有界限制

- Python 3.12 和锁定依赖不可用时，性能及跨进程身份 section 必须报告 `incomplete`，不可伪装为正式通过。
- 缺失或变更的真实代表性 PNG/manifest/golden-vector artifact 必须保留 `incomplete` 或 `failed` 状态。
- 没有独立物理真值的真实图像只提供行为、身份、重复性和质量契约证据，不证明绝对尺寸准确度。
- 用户验收结论应记录在父 Issue #10；本文件和机器结果只提供证据与建议。

## 建议发布到父工单的摘要

请将一次 `run_complete_validation` 的实际 JSON 结果、运行环境、资产哈希、section 状态和上述有界限制发布到 Issue #10，交由用户逐项接受或退回。不要把部分绿色测试、skip 或测试数量解释为完整验证通过。
