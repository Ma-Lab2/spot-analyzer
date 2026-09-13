---
issue: 8
status: accepted
contract: export-contract-v1
date: 2026-09-09
---

# 结果报告与导出数据契约

> **后继工作流通知（2026-09-13）**：`workflow-contract-v1`（Issue #75/ADR-0002）新增预览结果、正式结果和需要重新计算状态。本文的单一 `AnalysisRecord`、指标值/状态/原因码、`null` 门控、版本化 schema、输入只读和资产身份规则继续有效；预览或需要重新计算的记录不能作为当前测量报告源。默认 PNG、可选 PDF 和多图片逐项导出属于后继产品工作流，不改变本文件的机器字段语义。

## 1. 决议范围

本决议冻结一次 `分析记录` 的人类可读报告、结果图资产以及未来机器可读 JSON/CSV 导出的稳定边界。它不实现报告生成器、JSON Schema 校验器或 CSV writer；实现必须以本契约和 `issue-6-spec.md` 为准。

本决议与既有决策的关系：

- `issue-6-spec.md` 冻结测量语义、指标、状态、原因码、历史记录和对比兼容性；本文件只冻结它们如何被持久化和呈现。
- `docs/adr/0001-analysis-core-windows-client-boundaries.md` 冻结分析核心、worker 和 PDF/PNG 报告 seam；本文件补充 `AnalysisRecord`、报告资产和未来 JSON/CSV 的字段契约。
- JSON/CSV 历史导出不加入当前 MVP 的交付范围，但现在冻结其 schema，避免核心、CLI、UI 和后续历史功能各自发明格式。

本票已确认以下三项实现选择：

1. `analysis_fingerprint` 使用 RFC 8785（或完全兼容的实现）对规范化 JSON 计算内容摘要。
2. JSON/CSV 的机器单位使用 ASCII 稳定码（如 `um`、`deg`）；人类报告使用 `µm`、`°` 等可读符号。CSV 使用大写 `NULL` 作为 v1 的显式 null 标记。
3. JSON/CSV 契约现在冻结，但属于 MVP 之后的历史导出能力；当前 MVP 只实现消费同一 `AnalysisRecord` 的 PDF/PNG 报告。

## 2. 单一事实源

一次分析只能有一个不可变的 `AnalysisRecord`。PDF、PNG、JSON 和 CSV 都是该记录的不同视图或导出物，不得各自重新计算指标，也不得从显示图像反推测量值。

```text
InputImage + AnalysisConfiguration
             │
             ▼
       AnalysisRecord
        ├── PDF/PNG 人类报告
        ├── JSON 机器记录
        └── CSV 指标表
```

记录必须同时保存：

- 不可变 `record_id`；重新分析生成新 ID，不覆盖旧记录；
- `analysis_fingerprint`：输入内容哈希、完整实际配置、契约/profile/算法版本的规范化摘要；
- 输入资产身份和解码元数据；路径只是定位提示，内容哈希才是身份；
- 完整分析配置、结果、状态、原因码、版本和可重建/可核验的派生资产引用；
- 生成时间、软件构建和导出信息。生成时间不参与测量数值的确定性计算。

相同输入内容和相同完整配置必须得到相同的测量数值、指标状态、原因码、诊断和不含生成时间的派生测量资产内容；`record_id`、生成时间、报告渲染中的时间文本和输出路径可以不同。

## 3. JSON `analysis-record-v1`

### 3.1 顶层字段

JSON 使用 UTF-8、对象字段名为稳定的 `snake_case` 英文名。以下字段为 v1 固定顶层字段；未识别的新增字段可被读取端忽略，但不得改变已有字段的语义。

| 机器字段 | 类型 | 含义 |
|---|---|---|
| `schema` | string | 固定为 `analysis-record-v1` |
| `record_id` | string | 一次分析尝试的不可变身份 |
| `analysis_fingerprint` | string | 完整输入/配置/版本的规范化摘要 |
| `flow_status` | enum | `processing`、`input_invalid`、`parameter_invalid`、`analysis_failed`、`computed`、`exported` |
| `summary_status` | enum/null | 整次测量的 `valid`、`caution`、`invalid`、`unavailable`；无记录的流程失败为 `null` |
| `input` | object | 输入资产身份、解码和编码语义 |
| `calibration` | object | x/y 空间标定、单位、来源和确认状态 |
| `preprocessing` | object | 实际执行的预处理顺序、参数、版本和统计 |
| `analysis_region` | object | 用户确认的矩形区域及坐标语义 |
| `model` | object | 分析模型、profile、初始化和拟合配置 |
| `metrics` | object | 规范指标及各指标/坐标域的值、单位、状态和原因码 |
| `diagnostics` | object | 背景、掩膜、饱和、边界、多峰、拟合残差等诊断 |
| `derived_assets` | array | 原始/校正/拟合/残差/掩膜/报告资产引用 |
| `provenance` | object | 契约、profile、算法、软件构建和参数版本 |
| `export` | object | 本次导出的格式、报告名称、路径策略和时间 |

流程失败时可以输出同一 schema 的错误包，但 `summary_status` 和 `metrics` 为 `null`，并在 `diagnostics.errors` 中保存结构化错误。不得生成声称包含正常分析结果的 PDF/PNG。

### 3.2 输入、标定和配置

`input` 至少包含：

```json
{
  "asset_id": "input-...",
  "uri_hint": "file:///.../image.png",
  "sha256": "...",
  "read_at": "2026-09-09T...Z",
  "read_status": "decoded",
  "width": 1600,
  "height": 1200,
  "dtype": "uint8",
  "bit_depth": 8,
  "endianness": null,
  "channels": 1,
  "encoding": {
    "format": "png",
    "gamma": null,
    "srgb": null,
    "icc_profile": null,
    "semantic_confirmation": "relative_intensity_code"
  }
}
```

缺失的元数据使用 JSON `null`，并由 `missing_metadata` 或相应原因码说明；不得用空字符串、`0` 或猜测值代替。输入资产只读，记录中保存的 URI 不授予 writer 修改权限。

`calibration` 必须分别保存 `x` 和 `y`，每个方向包含：`value`、`unit_per_pixel`、`physical_unit`、`source`、`confirmation`（`confirmed`、`provisional`、`missing`）、`source_asset_id`（若有）和 `verified_at`。缺失标定仍可导出像素域结果，但物理域值必须为 `null`；`provisional` 物理结果最多为 `caution`。

`preprocessing` 保存规范顺序中每一步的 `name`、`executed`、实际参数、实现版本、输入/输出语义和统计。默认不滤波、不 DPC、不自动修复坏点；显式高级分支必须同时保存标准分支与高级分支的敏感性比较。

`analysis_region` 保存 `kind: "rectangle"`、`x`、`y`、`width`、`height`、坐标约定、是否用户确认、是否接触图像边界以及 ROI 外背景区域的独立描述。不得用裁剪后的数组尺寸代替原图坐标。

### 3.3 指标对象

每个指标必须拥有稳定机器键，并至少包含：

```json
{
  "value": 12.34,
  "unit": "px",
  "status": "caution",
  "reason_codes": ["provisional_profile", "possible_saturation"],
  "method_version": "moment-d4sigma-v1"
}
```

有像素域和物理域的指标使用 `domains`，各域独立门控：

```json
{
  "domains": {
    "pixel": {
      "value": 12.34,
      "unit": "px",
      "status": "valid",
      "reason_codes": [],
      "method_version": "..."
    },
    "physical": {
      "value": null,
      "unit": "um",
      "status": "unavailable",
      "reason_codes": ["calibration_missing"],
      "method_version": "..."
    }
  }
}
```

固定指标键为：

- `peak_center`；
- `gaussian_fwhm_major`、`gaussian_fwhm_minor`、`gaussian_ellipticity`、`gaussian_angle`；
- `moment_d4sigma_major`、`moment_d4sigma_minor`、`moment_angle`；
- `ee50`、`ee80`、`concentration_rref`；
- `fit_residual`、`centroid_offset`；
- `quality_diagnostics`。

实现可以保存诊断细节，但不得通过新增同义键改变上述指标语义。`concentration_rref` 必须连同 `rref` 的值、单位、来源和确认状态保存，不能称为 Strehl。

### 3.4 值、状态和原因码

对外 JSON/CSV/PDF/PNG 的门控规则一致：

| 状态 | 对外值 | 含义 |
|---|---|---|
| `valid` | 数值或结构化值 | 满足该指标的质量条件；若 profile 为 `provisional`，仍按契约封顶为 `caution` |
| `caution` | 数值或结构化值 | 可解释但必须展示全部原因码 |
| `invalid` | `null` | 已尝试计算但质量条件不成立 |
| `unavailable` | `null` | 不适用或缺少计算前提 |

`invalid` 与 `unavailable` 的内部尝试值只能留在明确标记为 `non-reportable` 的调试产物中，普通导出不得复用。`reason_codes` 必须是完整、稳定、可枚举的字符串数组，保留所有原因而不只保留最高严重性；顺序按契约定义的严重性和稳定键排序。

警告可以在 `diagnostics.warnings` 中提供结构化内容：`code`、`severity`、`label_key`、`message_args`、`affected_metrics`。机器字段不把翻译后的文字作为身份；人类显示由版本化 `label_key` 和当前语言包生成。

### 3.5 版本和规范化

`provenance` 至少包含：

```json
{
  "export_contract": "export-contract-v1",
  "analysis_contract": "analysis-contract-v1",
  "standard_profile": "standard-profile-v1",
  "quality_profile": "quality-profile-v1",
  "profile_validation": "provisional",
  "algorithm_version": "...",
  "software": {"name": "spot-analyzer", "version": "...", "build": "..."},
  "parameter_snapshot_sha256": "..."
}
```

JSON 对外不得出现 `NaN`、`Infinity` 或依赖语言的日期/枚举对象；数值使用 JSON number，日期使用 UTC RFC 3339 字符串。用于 `analysis_fingerprint` 的配置序列化采用 RFC 8785 规范 JSON（或完全兼容的实现）：UTF-8、对象键按规范排序、数组顺序保持语义顺序、无空白、浮点格式固定。实际实现和版本必须写入 `provenance`。

## 4. CSV `analysis-metrics-v1`

CSV 是 JSON `metrics` 的扁平、可筛选视图，不是第二套数据源。采用 UTF-8、逗号分隔、首行固定表头、RFC 4180 引号规则和 `.` 小数点。固定列顺序为：

```text
schema,record_id,analysis_fingerprint,metric_key,domain,value,unit,status,reason_codes,method_version
```

约束：

- 每个指标每个可用坐标域一行；非坐标域指标使用 `domain=common`；
- `value` 只放一个标量或 JSON 编码的结构化值；`invalid`/`unavailable` 必须写 `NULL`；
- 空字符串不是缺失值。`NULL` 是 v1 保留的 null 标记，若未来允许真实字符串值 `NULL` 必须升级 schema；
- `reason_codes` 使用紧凑 JSON 数组字符串，例如 `["calibration_missing"]`，以保证多原因可无损解析；
- `unit` 永不省略。机器单位使用 ASCII 稳定码：`px`、`um`、`um/px`、`deg`、`fraction`、`count`、`code_value`；人类报告可以显示 `µm`、`°` 等本地化符号；
- CSV 不复制原始像素、校正数组或内部拟合尝试值；图像和派生矩阵通过 JSON 资产清单引用。

CSV 只表示指标表。输入元数据、完整配置、诊断和资产身份以同一 `record_id` 的 JSON 为准；不能把 CSV 单独当作可复现分析记录。

## 5. 结果图和人类报告

PDF 和 PNG 使用同一 `ReportPackage` 和同一 `record_id`，只改变版式和容器，不改变结果语义。结果图可以包含原始、校正、拟合、残差、掩膜和剖面等显示图像，但它们必须标明图层名称、坐标标尺、ROI 和规范中心；显示色图、拉伸、插值和缩放不得回写 `AnalysisRecord`。

报告至少展示：

1. 用户指定的报告名称、`record_id`、生成时间和流程状态；
2. 输入图像预览、尺寸、位深、通道、编码语义和 SHA-256；
3. x/y 空间标定、来源及 `confirmed/provisional/missing` 状态；
4. 按规范顺序排列的预处理、参数、版本、背景/坏点/饱和统计；
5. 用户确认的分析区域、规范中心及质心/拟合中心诊断；
6. 模型、拟合质量、残差和可识别的掩膜视图；
7. 指标值、单位、每指标状态和全部原因码；被门控值显示 `N/A`，不得泄露内部尝试值；
8. 契约、profile、算法和软件版本，以及派生资产哈希。

人类可读标签使用简体中文并保留 `FWHM`、`D4σ`、`EE50`、`µm/pixel` 等国际符号。机器键始终使用本文件的英文名；标签文本可翻译，不能作为 schema 身份。报告名称由用户指定，可选时间后缀；输出目录独立于输入目录，输入文件保持只读，同名文件自动追加序号而不覆盖。

每个派生资产至少包含：`asset_id`、`kind`、`record_id`、`uri`、`sha256`、`format`、`width`/`height`（适用时）、生成参数和生成版本。路径丢失但哈希仍可验证时，记录和指标仍可读取；哈希不匹配时禁止图像级对比和重分析。

## 6. 缺失、失败和可复现性

- 解码失败、输入无效、参数无效和分析失败不产生正常测量报告；机器端返回结构化失败状态，`record`/`metrics` 为 `null`。
- 计算完成但单项无效仍可导出完整记录；该指标对外值为 `null`，并保留状态和全部原因码。
- 缺少输入元数据、空间标定、Rref 或派生资产时，按字段规则使用 `null` 和明确状态，不用默认值伪装完整性。
- 原始输入永不覆盖；所有 JSON、CSV、PDF、PNG 和派生资产写入独立输出目录，并采用临时文件加原子重命名。
- 记录不复制原始图像像素；外部原始图像通过 URI 提示和内容哈希关联。当前路径不可访问不允许自动寻找同名替代文件。
- 同一输入哈希、完整配置和版本可重建同一测量结果。环境差异不得静默改变记录语义；软件构建和依赖清单必须可追溯。

## 7. 版本迁移

`schema`、`export_contract`、`analysis_contract`、profile 和算法版本分别管理：

- **patch**：不改变字段语义或数值解释，只修正文档/序列化缺陷；读取端可透明接受；
- **minor**：只增加可选字段或诊断，旧读取端忽略未知字段；新读取端对缺失字段使用“未提供”而不是猜测；
- **major**：字段删除/重命名、单位/公式/状态含义变化或兼容性破坏，必须使用显式迁移工具；
- 公式、阈值、默认参数或状态映射变化必须生成新的契约/profile/算法版本，并把已有工作区结果标为 `stale`，不得静默重算或重新判定旧记录；
- 迁移只转换结构，不重新分析原始图像、不补造缺失值、不把 `invalid`/`unavailable` 提升为有效状态；迁移后的文件保存 `source_schema`、`migration_path`、`migration_tool_version` 和 `migrated_at`；
- 历史记录始终按原版本解释。若新版本需要重新分析，生成新的 `record_id` 并保留旧记录。

## 8. 验收清单

- [ ] PDF/PNG/JSON/CSV 对同一分析共享不可变 `record_id`，且 JSON 是机器记录唯一事实源。
- [ ] 同一输入哈希和完整配置重复运行时，指标、状态、原因码和不含生成时间的派生测量资产内容一致。
- [ ] 输入文件只读，输出写入独立目录，同名不覆盖并且写入是原子的。
- [ ] 每个指标包含值、单位、状态、完整原因码、方法版本和 null 门控结果。
- [ ] 缺失、`invalid` 和 `unavailable` 不使用空字符串、0 或内部尝试值伪装。
- [ ] x/y 标定、来源和确认状态独立保存；物理域值按标定状态门控。
- [ ] CSV 表头、列顺序、单位码、`NULL` 标记和原因码编码固定且可逆。
- [ ] 报告显示输入、标定、预处理、ROI、模型、诊断、版本和门控后的值；显示图像处理不改变测量数据。
- [ ] 外部资产以内容哈希识别；路径失效可读记录但禁止图像对比和重分析，哈希不匹配同样禁止。
- [ ] schema/profile/算法变更有明确版本；迁移不重新解释历史记录、不补造值、不改变原 `record_id`。

## 9. 非目标

本决议不实现 JSON/CSV 历史 UI、批处理、逐像素图像对比、自动配准、原始像素嵌入或新的测量指标。它也不改变 `issue-6-spec.md` 已冻结的指标公式、质量门槛和报告门控。