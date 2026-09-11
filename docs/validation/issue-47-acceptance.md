# Issue #47 跨 seam 统一验证交接记录

本票提供 `spot_analyzer.validation.run_issue47_validation()` 作为统一验证入口。入口固定报告 `synthetic`、`low_snr`、`real_fixtures`、`identity`、`report`、`ui_worker_parity` 和 `performance` 七个 section；每个 section 的状态只能是 `passed`、`failed` 或 `incomplete`，缺失证据不会静默跳过。

性能 section 的正式 workload 固定包含 256×256 sanity 与 1024×1024 主负载，默认十次重复，并记录 Python 版本、依赖版本、操作系统、硬件、图像尺寸、重复次数及冷/热模式。只有 Python 3.12 和锁定依赖同时满足时，结果才可作为正式性能证据；目标为核心热运行 p95 ≤2 秒、worker 冷启动/PNG 解码/分析/派生资产写出端到端 p95 ≤5 秒。

UI/worker parity 明确比较配置、输入 metadata、指标、流程状态、指标有效性、原因码、diagnostics、mask statistics、派生资产身份和 analysis fingerprint。当前无 WPF SDK/runtime 的环境将该 section 标为 `incomplete`，而不是宣称通过。

## 解释边界

- `standard-profile-v1` 与 `quality-profile-v1` 保持 `provisional`，本票不升级 profile。
- 真实 PNG 缺少独立物理真值时，只声明行为与接口证据，不声明绝对物理尺寸准确度。
- 本票是 MVP 正式开发交接证据，不是 WPF 发布包或正式产品发布声明。

## 五项已接受范围内的 bounded limitations

1. `independent_physical_ground_truth_absent`：真实图像无法建立绝对物理尺寸准确度。
2. `historical_acquisition_metadata_unrecoverable`：历史采集条件不可恢复。
3. `rgb_acquisition_timezone_unknown`：RGB 资产本地时间的时区未知。
4. `png_color_management_matrix_absent`：没有覆盖 gAMA/sRGB/iCCP 组合矩阵。
5. `acquisition_condition_matrix_absent`：没有控制曝光、增益、温度、光路、焦距和批次的物理真值矩阵。

所有 limitation 都必须进入机器可读结果并由用户验收明确接受或要求修改规格。
