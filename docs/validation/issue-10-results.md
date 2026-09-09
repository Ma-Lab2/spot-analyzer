# Issue #10 验证结果（当前原型轮次）

日期：2026-09-09

## 当前实现

本轮建立了可直接运行的 Python 原型和回归测试：

- `spot_analyzer.synthetic`：固定版本、固定随机源的合成场景生成器；
- `spot_analyzer.oracle`：圆高斯解析 oracle、离散 EE oracle 和角度/轴关系；
- `spot_analyzer.validation`：manifest JSON loader、标量容差比较器和结构化回归结果；
- `spot_analyzer.core.analyze`：内存 `InputImage` 到 `AnalysisRecord` 的单一分析 seam；
- `spot_analyzer.input.decode_png`：8/16 位灰度 PNG 适配器、IHDR 位深核对、16 位 PNG 网络字节序记录、PNG 格式检查、RGB 通道一致性记录、输入 URI/元数据快照和 expected SHA-256 核验；
- `spot_analyzer.worker`：`analysis-request-v1` / `analysis-event-v1` / `analysis-result-v1` 的资产路径或 file URI + expected hash NDJSON 路径；协议拒绝跨进程内联整幅像素数组，要求完整配置快照和输出策略，并把派生数组原子写入运行目录后返回 URI/hash/生成参数；
- `spot_analyzer.report`：共享 `record_id` / `analysis_fingerprint` 的不可变 `ReportPackage` PNG/PDF 原型，展示输入、校正、正信号、拟合、残差、mask、中心剖面和能量曲线，记录输出 SHA-256，并用独占占位和原子替换避免并发同名覆盖；
- `spot_analyzer.core`：加入版本化坏点坐标、统一测量有效 mask、背景/拟合/残差 mask 统计及哈希；
- `tests/`：核心、适配器、真实 worker 子进程、报告、manifest、真实 fixture 分析和 32 个低 SNR 固定种子测试。

## 测试结果

```text
python -m pytest -q
78 passed

python -m compileall -q spot_analyzer tests
通过，无输出

VS Code diagnostics
0 diagnostics
```

已覆盖：

- 圆高斯 FWHM、D4σ、EE50 和中心解析容差；
- 旋转椭圆高斯主次轴与角度；
- 双峰和 Airy 旁瓣诊断的原型行为；
- 背景梯度、饱和和裁边的状态/原因码行为，以及显式坏点坐标不修复、统一有效域、核心有效比例和 mask hash；
- 固定噪声种子 `0..31` 的 SNR 门控分布：14 个 `caution`、18 个 `invalid`；18 个 `SNR<5` 用 `low_snr` 门控为 null，14 个 `5≤SNR<10` 用 `low_snr_caution` 降级。可用 FWHM 尝试值偏差中位数 `1.8220 px`、MAD `0.8007 px`、95 分位绝对偏差 `3.7593 px`；这些聚合只描述固定扰动场景，不把无效尝试值对外报告；
- 未确认 ROI、背景区域、越界坏点和错误 profile 提升的结构化参数失败；
- PNG 强度语义确认、PNG 格式与 IHDR 位深检查、8/16 位样本保真、RGB 通道一致性、元数据快照、URI 和输入哈希；
- 五个外部灰度 fixture 的完整 decode + analyze 重复回归，断言输入 SHA-256、配置快照、状态、必需/禁止原因码和 fingerprint 稳定性；RGB 显示 fixture 按 manifest 排除测量回归；
- worker 真实子进程 stdout 纯 NDJSON、资产 hash mismatch、worker 与直接核心调用的指标/诊断/`analysis_fingerprint` 一致性；显示设置不进入分析配置且不改变测量结果；
- 报告共享记录身份、门控后的值、不可变 package、输出内容 SHA-256、规格匹配和并发安全的同名不覆盖策略。

## 尚未完成的验收项

本结果不是 profile 验证通过声明：

1. 真实 PNG 回归已经覆盖 manifest 中五个灰度 fixture，但这些样例没有独立物理真值，因此只作为解码、重复性和质量警告证据，不作为绝对尺寸金标准；
2. PNG 已覆盖 8/16 位、IHDR 位深、16 位 PNG 网络字节序和基础元数据快照；尚未建立包含 gAMA/sRGB/iCCP 等全部组合的固定 fixture 矩阵；
3. 报告已加入基础审计视图，但还缺坐标标尺、ROI/中心叠加、完整图例、派生报告资产清单和更严格的 PDF/PNG 语义结构检查，仍不是 Issue #8 的最终报告；
4. worker 已实现资产引用、expected hash、完整请求快照、真实子进程 NDJSON 和运行目录派生资产写出；取消、超时、强制崩溃、stderr 诊断和导出失败烟测仍需实现；
5. 当前只验证直接核心与 worker/CLI seam 的一致性；仓库中尚无 WPF 调用层，因此真正的 UI/CLI 端到端一致性仍待 Windows 外壳实现后验证；
6. 高级预处理当前被显式拒绝；标准/高级双分支和敏感性比较尚未实现。匹配背景帧优先路径也尚未实现，当前只支持经确认且与分析区域不重叠的保护区仿射背景；
7. 物理域已覆盖缺失标定门控、方形像元换算、非方形像元主轴协方差变换和物理域 EE 重算；拟合 covariance/不确定度传播仍未实现；
8. `analysis_fingerprint` 仍使用稳定排序 JSON 原型，尚未替换为完整 RFC 8785 兼容实现；
9. `pyproject.toml` 已固定 Python 3.12 范围和当前依赖版本，但本轮实际测试解释器仍为 Python 3.10.11；Python 3.12 构建、PyInstaller one-folder worker、.NET 8 self-contained 外壳和无开发环境的干净 Windows portable ZIP 烟测尚未执行。

当前实现还将 `profile_validation` 锁定为 `provisional`，拒绝由请求配置直接提升为 `validated`。因此 `standard-profile-v1` 和 `quality-profile-v1` 继续保持 `profile_validation: provisional`。即使数值测试通过，对外 `valid` 也按契约封顶为 `caution`；Issue #11 正式审查接受前不升级为 `validated`。
