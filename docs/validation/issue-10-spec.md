# Issue #10：规范算法代表性验证验收规格

## Problem Statement

用户需要确认规范焦斑分析在合成图像和真实代表性图像上的行为是否科学合理、可解释并且足够快速。当前代码已经完成分析核心、输入适配器、worker 和基础报告原型，但验证证据仍分散在多个测试和文档中，性能没有正式基线，真实 fixture 在不可用时可能被静默跳过，报告语义也缺少完整的机器可检查验收。

Issue #10 的目标不是再次设计测量公式，而是把 `issue-6-spec.md` 的测量契约、`issue-8-spec.md` 的报告契约和 `issue-9-spec.md` 的验证契约组织成一次可重复运行、可审计的验证结果。验证完成不等同于 profile 已升级为 `validated`，也不等同于 Windows 客户端发布完成。

## Solution

建立一个最高层的 Issue #10 验证聚合入口，统一执行并返回 JSON-ready 的结构化结果，内部复用现有分析核心、真实 fixture manifest、worker、报告和 RFC 8785 seam。

验证聚合结果必须分别报告以下项目：

1. 合成场景和解析 oracle 比较；
2. 固定种子低 SNR 扰动验证；
3. 真实代表性 PNG fixture 验证；
4. 核心分析和 worker 端到端性能基线；
5. 报告结构化语义验证；
6. RFC 8785 fingerprint 跨进程 golden-vector 验证；
7. 验证环境、版本、资产哈希和未完成项。

每个项目的状态只能是 `passed`、`failed` 或 `incomplete`。真实 fixture、Python 3.12 或 golden vector 不可用时必须明确为 `incomplete`，不得通过 skip 隐藏。

性能基线固定为声明环境中的 `1024×1024` 主负载，并补充 `256×256` sanity benchmark：核心 `analyze` 热运行 p95 目标不超过 2 秒；worker 冷启动、PNG 解码、分析和派生资产写出端到端 p95 目标不超过 5 秒。结果必须记录 CPU、内存、操作系统、Python、NumPy、SciPy、Pillow、图像尺寸、重复次数和冷/热运行模式。当前不符合项目 Python 3.12 约束的解释器只能用于开发检查，不能生成正式性能结论。

真实 PNG 使用受控 artifact 的 manifest 和 SHA-256。没有独立物理真值时，验证只声明解码、身份、重复性、状态、原因码、可解释性和接口一致性，不声明绝对尺寸准确度。

报告验证必须确认输入、校正强度图、正信号图、拟合图、残差、mask、中心剖面、拟合剖面、半径—累计能量曲线、ROI/中心、坐标单位、质量状态、门控值、provenance 和派生资产身份均来自同一 `AnalysisRecord`，且普通报告不泄露内部拟合尝试值。

Issue #10 完成前，`standard-profile-v1` 和 `quality-profile-v1` 继续保持 `profile_validation: provisional`。用户验收结论必须同时记录在 GitHub Issue #10 和仓库验证记录中。

## User Stories

1. As an algorithm maintainer, I want to run the complete Issue #10 validation from one aggregation seam, so that all acceptance evidence uses the same configuration and environment.
2. As a scientist, I want clean circular Gaussian results compared with an analytic oracle, so that center, FWHM, D4σ and EE measurements have quantitative evidence.
3. As a scientist, I want rotated elliptical Gaussian results compared with known axes and angle, so that anisotropic calibration and axis ordering are verified.
4. As a quality reviewer, I want multimodal and Airy/ring counterexamples, so that the system does not present an unsuitable single-Gaussian fit as a valid measurement.
5. As a quality reviewer, I want gradient, bad-pixel, saturation and cropped-edge scenarios, so that failure rules and reason codes are demonstrated rather than assumed.
6. As a quality reviewer, I want fixed-seed low-SNR runs, so that quality gating, repeatability and diagnostic distributions are reproducible.
7. As an operator, I want missing or changed real fixture assets to produce an explicit incomplete or identity failure, so that absent evidence cannot appear as a passing validation.
8. As an operator, I want real fixture provenance and acquisition metadata recorded, so that representative-image conclusions can be traced back to their source assets.
9. As a reviewer, I want real images without physical ground truth to be labelled as behavioral evidence only, so that the report does not overclaim absolute measurement accuracy.
10. As a performance reviewer, I want core and worker timing reported separately, so that algorithm latency is not confused with process startup or file I/O latency.
11. As a performance reviewer, I want timing results tied to a declared Python 3.12 environment, so that release conclusions are reproducible.
12. As a report consumer, I want actual and fitted center profiles to share a meaningful pixel axis, so that their locations and widths can be compared honestly.
13. As a report consumer, I want the energy curve to preserve radius and physical units, so that EE50 and EE80 have an interpretable graphical context.
14. As an auditor, I want report provenance to include contract, profile, algorithm, software, build and parameter snapshot identity, so that a result can be reproduced.
15. As an auditor, I want ordinary reports to exclude internal fit attempts and exploratory comparison values, so that diagnostic implementation details are not mistaken for reportable measurements.
16. As an integration maintainer, I want direct-core and worker results checked for semantic parity, so that a future CLI or Windows UI cannot silently use a different analysis path.
17. As a release engineer, I want RFC 8785 fingerprints checked with cross-process golden vectors, so that identity remains stable across supported Python 3.12 runs.
18. As a project owner, I want every validation item to state passed, failed or incomplete, so that an open gap is never hidden by a green partial test run.
19. As a project owner, I want the user decision about accepting or changing the analysis specification recorded, so that closing the issue reflects an explicit human decision.
20. As a future client developer, I want this validation evidence to be independent of the WPF shell, so that the portable client can reuse a verified core and worker rather than reimplementing measurement logic.
21. As a release reviewer, I want Issue #10 completion separated from portable-client completion, so that algorithm evidence and packaging evidence remain independently auditable.
22. As a profile maintainer, I want provisional status preserved until all required evidence and the later formal review are complete, so that passing numerical tests cannot prematurely imply validated scientific status.

## Implementation Decisions

- The highest validation seam is one aggregation operation for Issue #10. It composes existing synthetic regression, low-SNR regression, real-fixture validation, direct analysis, worker process execution, report preparation/export, and fingerprint checks.
- Existing analysis and report seams remain the owners of measurement semantics. The validation layer compares outputs and inspects contracts; it does not calculate alternative metrics or rebuild report curves.
- Synthetic validation uses the existing deterministic scene generator and oracle definitions. Required scenarios and tolerances remain those frozen by the analysis and validation contracts.
- Real fixture validation uses a versioned manifest, relative asset identity, expected SHA-256, analysis/background regions, expected statuses, required reason codes and forbidden reason codes. External assets are read-only and are not silently replaced.
- Missing real fixtures or missing verification artifacts produce `incomplete` validation status. Ordinary development may continue synthetic checks, but a release/profile validation result cannot be reported as passed when required real evidence is unavailable.
- The formal performance workload includes `256×256` and `1024×1024` images. Core timing and worker end-to-end timing are reported separately, with ten repetitions, p95 as the primary statistic, and environment metadata captured alongside results.
- Formal performance evidence requires Python 3.12 and the locked project dependencies. Python 3.10 or 3.14 results are development observations only.
- Report semantic validation consumes the same immutable `AnalysisRecord` and `ReportPackage` used by export. It verifies axes, units, masks, provenance, gate behavior, asset identity and absence of non-reportable internal trial values.
- Profile validation remains provisional throughout Issue #10. No implementation in this spec may promote a profile to `validated`.
- User acceptance is recorded as a decision summary on GitHub Issue #10 and as a detailed repository validation record.
- The portable Windows client is a separate follow-up specification. It must later reuse the validated core and worker interfaces, but its WPF, PyInstaller, ZIP and clean-machine work is not part of this spec.

## Testing Decisions

- Tests assert externally observable behavior: structured validation status, metric comparisons, reason codes, report package contents, exported artifact identity, timing result shape and fingerprint stability. They do not assert private helper call order or implementation details.
- Existing synthetic tests and validation helpers are the prior art for deterministic oracle comparison, fixed-seed aggregation, status gates and reason-code contracts.
- Existing real fixture tests are extended so required release validation distinguishes unavailable evidence as `incomplete` rather than silently skipping it.
- Existing adapter tests are the prior art for PNG semantics, worker NDJSON, direct-core/worker parity, report export, derived asset hashes and cancellation/timeout behavior.
- Performance tests record structured measurements rather than enforcing a fragile wall-clock assertion on every developer machine. The declared p95 targets are evaluated in the formal Python 3.12 verification environment.
- Report tests verify both `ReportPackage` structure and PDF/PNG export semantics. They check record identity, user report name, process and measurement states, coordinate axes, units, gate values, provenance and derived assets without requiring byte-for-byte equality between exports.
- Fingerprint tests run representative payloads through separate processes and compare canonical output and digest against fixed golden vectors. Non-finite values must not collapse distinct metadata into the same fingerprint.
- The complete validation run must expose per-section results and an overall status. A skipped required section is represented as `incomplete`, not `passed`.

## Out of Scope

- Changing the mathematical definitions frozen by `issue-6-spec.md`.
- Replacing the standard analysis branch with an exploratory advanced branch.
- Formal uncertainty propagation for EE, angle or ellipticity; this remains assigned to Issue #11 unless separately accepted.
- Upgrading either profile from `provisional` to `validated`.
- Building the WPF/.NET 8 application shell.
- PyInstaller packaging, portable ZIP construction and clean Windows release smoke tests.
- Single-file EXE packaging.
- Installing or provisioning Python 3.12 automatically on a developer machine.
- Adding network-based fixture download to ordinary unit tests.
- Establishing absolute physical accuracy for real images without independent physical ground truth.
- Batch analysis, camera acquisition, automatic image registration or additional analysis models.

## Further Notes

- The current implementation baseline is commit `7a6d903` (`Complete Issue 10 review fixes`).
- The current host has representative external fixtures available, but the active Python interpreter is 3.10.11 and therefore is not the formal delivery environment declared by the project.
- The current repository has no performance benchmark infrastructure; implementing the aggregation seam and its result schema is the first step before collecting formal timing evidence.
- Issue #10 is considered complete only when all in-scope sections are passed or explicitly accepted as bounded limitations, required evidence is available, and the user records an acceptance decision. Until then the correct state is `implementation-fixed` plus `validation-incomplete`.
- The later portable-client release package is expected to use an EXE entry point inside a portable `win-x64` package, with the Python worker and its dependencies shipped beside it. That package must preserve the same analysis record, worker protocol and provenance semantics.
