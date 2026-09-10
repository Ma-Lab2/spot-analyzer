# Issue #10 code review record

日期：2026-09-10  
固定点：`dd23203`  变更范围：`dd23203..HEAD`

## Standards review

未发现具体规范违规或与规范相关的测试缺口。`git diff --check dd23203..HEAD` 通过。

## Spec review

审查依据为 Issue #10、仓库中的分析契约及 `docs/validation/issue-10-results.md`。以下问题属于实现范围内缺陷，阻塞 Issue #10 验收：

### 高严重度

1. **配置中的 profile 阈值未被算法使用。** `PreprocessingConfiguration` 接受并记录 SNR、core validity、localization、multiple-peak 和 background-fitting 阈值，但核心路径使用硬编码值，造成 provenance 与实际行为不一致：`spot_analyzer/models.py:128-160`; `spot_analyzer/core.py:325-335,913,946-948,975-978,1001-1021`。
2. **高级预处理敏感性比较指标和门控不符合契约。** 当前只比较初始化得到的 major-FWHM；超过 10% 直接硬无效，未对每个核心指标提供标准/高级值、绝对/相对差异及 per-metric 状态：`spot_analyzer/core.py:984-999,1078-1088,1309-1317`; `tests/test_adapters.py:248-273`。
3. **匹配背景可能错误接受缺失的主图采集 ROI。** 主图 ROI 缺失时被替换为分析区域，可能与具有显式 ROI 的不相关背景帧匹配；采集时间也不在匹配字段中：`spot_analyzer/core.py:156-168`; `tests/test_adapters.py:216-245`。
4. **匹配背景中的无效像素未并入 measurement-valid mask。** 只要背景帧存在一个有限像素就接受，但有效 mask 只依据主图；校正值可为 NaN 而对应 mask 仍为 True：`spot_analyzer/core.py:190-205,831-864`。
5. **未提供显式 SHA-256 的不同背景数组会产生相同 fingerprint。** 背景 fingerprint 输入只有 `sha256: null`、形状和元数据，未包含像素内容：`spot_analyzer/core.py:645-663`。
6. **模型接受的 NumPy 整数坐标会使 RFC 8785 fingerprint 生成崩溃。** `np.integer` 坐标未归一化为 Python 标量，canonicalizer 拒绝 `numpy.int64`：`spot_analyzer/models.py:216-227`; `spot_analyzer/core.py:635-642,674`。

### 中严重度

7. **FWHM 不确定度可能绑定错误轴且物理变换不正确。** 不确定度按标准误数值大小选择 major，而拟合按 sigma 轴判定；物理不确定度使用与角度无关的 x/y RMS，未传播旋转和协方差：`spot_analyzer/core.py:618-625,1260-1278`; `tests/test_validation.py:75-80`。
8. **请求 matched-frame 但未提供背景帧时静默降级。** 未记录 `background_match_unverified` 或缺失原因：`spot_analyzer/core.py:148-155,801-804,844-862`。
9. **匹配背景报告错误宣称执行了 affine background。** 报告固定写入 `affine_background`，即使实际使用匹配帧：`spot_analyzer/report.py:99-109`。
10. **部分导出失败未转换为结构化 `export_failed`。** 目标保留和 render 发生在保护块外，且 `_reserve_target` 只捕获 `FileExistsError`：`spot_analyzer/report.py:279-289,308-315`。

## Explicitly incomplete acceptance items

- 尚无 Issue #10 的用户验收决策。
- 真实 fixture 没有独立物理真值；PNG 的完整 `gAMA`/`sRGB`/`iCCP` 组合矩阵、完整报告语义检查和派生资产清单仍缺失。
- worker 取消/超时仍为协作式；无强制终止语义。
- 尚无 WPF 调用层端到端一致性证据。
- EE、角度和椭圆率物理域不确定度传播未完成。
- 尚未执行 Python 3.12 golden vectors、PyInstaller、.NET 8 self-contained 和 clean Windows portable ZIP 烟测。
- 两个 profile 继续保持 `provisional`，不得改为 `validated`。

## Verification

- `python -m pytest -o addopts='' -q` — **88 passed**
- `python -m compileall -q spot_analyzer tests` — 通过
- `git diff --check dd23203..HEAD` — 通过
- 审查未修改代码。

## Follow-up review

后续切片修复了第 8 项缺陷：缺失 matched frame 现在记录 `background_frame_unavailable` 并将 affine 降级标记为 `caution`。同时，标准 core/SNR/multiple-peak 质量门槛及背景拟合、定位参数均锁定为标准档案值，非法类型、非有限值和错误顺序会在配置阶段拒绝。新增回归测试后，完整套件为 **97 passed**。

本次 follow-up 已修复高级预处理敏感性指标和门控：标准/高级分支现在逐项记录 Gaussian FWHM、D4σ、EE50/80、C(Rref) 及相关形状指标的值、差异和两侧状态；`>10%` 仅将受影响指标置为 `caution`，`>20%` 置为 `invalid`，高级分支即使差异较小也至少为 `caution`。本轮完整套件为 **99 passed**。

后续切片已修复 FWHM 不确定度：记录拟合原始参数顺序及主/次轴映射，像素域不确定度跟随规范轴，物理域通过旋转椭圆、各向异性标定和完整协方差 Jacobian 传播。报告现在也会记录实际背景处理步骤，并将目标保留、渲染及写入异常转换为结构化 `export_failed`。EE、角度和椭圆率的不确定度传播仍未实现。
