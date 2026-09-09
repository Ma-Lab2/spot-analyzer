---
status: accepted
date: 2026-09-09
accepted: 2026-09-09
---

# 分析核心与 Windows 客户端的模块边界

Issue #7 的决定是：把测量语义集中在一个不依赖 UI、文件系统和网络通信的 Python 深模块中；命令行和 Windows UI 都通过同一个版本化的 worker interface 调用它。MVP 采用 Windows UI 外壳与分析 worker 分进程：WPF 外壳负责交互，独立 worker 负责一次分析及其绘图/报告资产；CLI 直接启动同一个 worker。worker 使用 stdin/stdout 上的版本化 NDJSON，而不是 localhost HTTP/RPC。这样既保留可直接调用的核心 seam，又让拟合崩溃、取消和资源泄漏不会拖垮 UI。

## 状态与适用范围

这是面向正式开发交接的已接受决策。范围是 Issue #6 已冻结的单张输入图像、一个用户确认的矩形分析区域、单核心主分析和 PDF/PNG 测量报告；不把 JSON/CSV 历史导出、批处理、相机采集或额外分析模型带入 MVP。

## 模块与 seam

### 1. 输入图像模块

输入图像模块把外部资产变成不可变的 `InputImage`。文件路径、文件句柄和 Pillow/OpenCV 细节都留在输入适配器内，分析核心只接收已解码数组和完整元数据。

```python
def decode_input(source: ImageSource,
                 policy: DecodePolicy) -> DecodeOutcome:
    ...
```

`DecodeOutcome` 显式区分解码成功、输入无效和解码错误，并携带：原始资产定位提示、SHA-256、尺寸、dtype、位深、端序（适用时）、通道、PNG 颜色/传递曲线元数据及其缺失状态。MVP 接受 8/16 位灰度 PNG；RGB 只有在三个通道完全一致且事实被记录时才可按灰度处理，不能静默执行 gamma、色彩转换或降位。测试使用内存输入适配器，因此不需要临时文件。

这是一个真实 seam：生产的 PNG 适配器和测试/合成的内存适配器满足同一接口。输入模块负责“输入图像”语义，但不负责背景扣除或显示拉伸。

### 2. 分析核心模块

分析核心是唯一拥有测量流程顺序、指标公式和质量门控的深模块。它同时完成预处理、独立中心定位、ROI 审查、旋转椭圆高斯拟合、D4σ、EE50/EE80、固定参考集中度、残差和质量诊断；调用者不应逐步调用这些算法函数。

```python
def analyze(image: InputImage,
            configuration: AnalysisConfiguration) -> AnalysisOutcome:
    ...
```

`AnalysisConfiguration` 是一次分析的不可变快照，包含：

- x/y 分开的 `SpatialCalibration`（物平面单位、来源、`confirmed`/`provisional` 状态）；
- 已确认的矩形 `AnalysisRegion`；
- 有版本的 `PreprocessingConfiguration`（背景来源、坏点掩膜、负值策略以及显式高级分支）；
- `AnalysisModel` 和标准/质量 profile 版本；MVP 只暴露已冻结的旋转椭圆高斯主模型；
- 独立于当前 FWHM 的 `Rref`（若使用）；
- 算法版本和完整实际参数。

`AnalysisOutcome` 不让调用者用异常猜测测量状态：

```python
@dataclass(frozen=True)
class AnalysisOutcome:
    flow_status: FlowStatus  # processing/input_invalid/parameter_invalid/...
    record: AnalysisRecord | None
    diagnostics: tuple[Diagnostic, ...]
```

预期的输入、参数和分析失败都返回结构化状态；只有程序错误或无法恢复的资源错误才越过这个接口抛出异常。若产生 `AnalysisRecord`，每个指标都必须带值、单位、状态、全部原因码、方法版本和 `null` 门控结果；`computed` 不等于 `valid`，profile 为 `provisional` 时不能把结果提升为规范 `valid`。重新计算生成新的 `record_id`，旧的分析记录不被覆盖；计算数值、状态、配置和版本在相同输入下保持确定性，生成时间和记录 ID 属于身份元数据。

核心不读取路径、不写文件、不创建 Qt 对象、不读取显示图像，也不自行发现空间标定。它可以保留供绘图和报告使用的校正强度图、拟合图、残差和掩膜派生数据，但这些派生数据仍由分析配置和输入确定，不能反向成为指标输入。

分析核心内部可以有以下 private seam，但不向 UI/CLI 暴露逐步算法接口：

- 标定校验：验证 x/y 值、单位、来源和确认状态；MVP 不自动推断空间标定；
- 预处理：按契约顺序生成有符号浮点校正强度图 `S`、`P=max(S,0)` 的积分视图和有效/坏点/饱和/负值掩膜；
- 测量：在同一确认分析区域上计算中心、D4σ、EE、固定 `Rref` 集中度、旋转椭圆高斯和残差；
- 质量判定：把输入诊断、采样、背景、边界、饱和、多峰和拟合证据映射为每指标状态及全部原因码；
- 记录组装：冻结配置、结果、资产身份、版本和门控值，形成 `AnalysisRecord`。

这些是实现内部的 module，而不是五个浅的公共 wrapper。这样替换背景估计或质量规则时，变化集中在分析核心；调用者只需学习一个 `analyze` interface。

### 3. 显示与绘图模块

绘图模块只把输入图像、校正强度图、模型、残差、掩膜和剖面渲染为 `DisplaySurface`；色图、分位数/全范围拉伸、插值、图例和叠加层属于显示图像，不改变 `AnalysisRecord`。

```python
def render_display(record: AnalysisRecord,
                    request: DisplayRequest) -> DisplaySurface:
    ...
```

`DisplayRequest` 可包含视图层、色图、显示范围、叠加层和尺寸；不能包含或修改分析参数。绘图测试应证明改变色图和拉伸后，记录中的测量值和校正强度图哈希不变。论文图预设不属于该 MVP seam。

### 4. 报告模块与输出适配器

报告模块把同一份分析记录变成两种视觉报告，不再为 PDF 和 PNG 维护两套结果语义：

```python
def prepare_report(record: AnalysisRecord,
                    specification: ReportSpecification) -> ReportPackage:
    ...

def write_report(package: ReportPackage,
                 destination: OutputDestination) -> ExportOutcome:
    ...
```

`ReportSpecification` 包含 PDF/PNG 格式、用户指定的报告名称、是否附加生成时间和输出目录；`prepare_report` 负责内容与版式，`write_report` 负责文件名清理、同名不覆盖、原子写入和独立输出目录。输入图像始终只读。PDF/PNG 都必须共享同一 `record_id`，并显著展示流程状态、每指标测量有效性、原因码、标定来源、预处理、ROI、模型、诊断和软件/算法版本；被门控指标显示为 `null`/N/A 及原因，不泄露普通用户报告不应看到的内部尝试值。

报告模块的 seam 允许 GUI 先预览 `ReportPackage`，CLI 再交给文件输出适配器；核心不直接写磁盘。JSON/CSV 机器历史记录未来可以消费同一 `AnalysisRecord`，但不在本 MVP 中实现。

## 两个调用者如何共用接口

### Worker interface

UI 与 CLI 共用的外部 seam 是一个版本化的 worker interface；传输只承载配置、资产引用和小型结果，不传输整幅校正数组。worker 自己通过输入适配器读取资产，并把绘图/报告产生的派生资产写入一次运行的工作目录，再在结果中返回 URI、哈希和生成参数。

```text
request:  {"schema":"analysis-request-v1", ...}
event:    {"schema":"analysis-event-v1", "kind":"started|progress|completed|failed", ...}
result:   {"schema":"analysis-result-v1", "flow_status":..., "record":..., ...}
```

请求必须包含输入资产的规范化 URI/路径提示和预期哈希、空间标定、完整预处理实际参数、已确认 ROI、模型/profile、Rref 以及输出策略。最终结果必须携带契约/profile/算法版本、输入资产身份、不可变 `record_id`、流程状态、每指标值/单位/状态/全部原因码/null 门控结果、诊断和派生资产引用。schema 不兼容、输入无效、参数无效、分析失败、取消和导出失败必须分别可识别；进程退出码只能表示 worker/协议是否完成，不能代替测量有效性。

worker 的 stdout 只输出可解析的 NDJSON 事件，stderr 只输出诊断日志；不得把日志混入结果。单次请求一个 worker，完成或失败后退出。这样脚本和 UI 共享同一个可记录、可回归的 interface，且大数组不会在 UI/CLI 与 worker 间复制。

### CLI 适配器

CLI 只负责解析命令行参数或完整请求文件、启动 worker、读取 NDJSON、保存输出资产并映射退出码。它不能实现背景估计、拟合、状态门控或文件名语义的副本。脚本化入口应支持传入完整配置快照，避免把一长串命令行默认值当成第二套契约。

### Windows UI 适配器

Windows UI 只负责把工作区交互映射到同一 worker interface：打开 PNG 走输入适配器；用户确认标定和分析区域后生成请求快照；点击分析启动 worker；结果面板读取 `AnalysisRecord`；图层切换请求派生显示资产；导出对话框构造 `ReportSpecification`。UI 自己只保存未提交的草稿配置，并在输入、标定、预处理、模型或 ROI 改变时把旧记录标为 `stale`，禁止继续显示为当前结果。

WPF 外壳用异步进程读取 worker 的 progress/completed/failed 事件；窗口对象、像素缓存和用户操作事件不得穿过 worker seam。UI 可在取消时终止 worker，不能把终止误报为分析失败或测量无效。

## 进程布局

```text
spot-analyzer.exe (WPF) ──┐
                          ├─ stdin/stdout NDJSON ─> spot-analysis-worker.exe
spot-analyzer-cli.exe ───┘                              │
                                                        ├─ InputImage adapter
                                                        ├─ analyze(AnalysisRequest)
                                                        ├─ render_display(...)
                                                        └─ prepare_report(...)
```

- WPF GUI 是长生命周期外壳；每次分析启动一个短生命周期 worker 进程。
- CLI 也是短生命周期调用者，启动同一个 worker；不能另行 import 一套指标实现。
- worker 进程拥有 Python/NumPy/SciPy 状态，核心函数在 worker 内同步执行；UI 不加载 Python 或 Qt。
- 不启动 localhost 服务，不引入端口、防火墙例外或常驻服务生命周期。
- worker 崩溃、超时或取消只影响当前分析；UI 显示结构化 worker 错误并保留旧分析记录。共享可变数组不得跨请求复用。
- 测试直接调用核心 `analyze`，再用独立协议测试验证 worker；因此进程隔离不牺牲核心的可测试性。

这个选择把 seam 放在领域 interface 和 worker transport 两层：核心 interface 为单元/回归测试提供 leverage，NDJSON interface 为两个真实调用者提供一致行为，进程隔离则集中处理取消和原生依赖崩溃。相比本地 HTTP，stdin/stdout 没有端口、发现、认证和网络依赖；相比把核心加载进 WPF，又避免科学运行时把 UI 进程变成唯一故障域。

## 技术栈与交付

MVP 推荐把科学计算和 Windows 外壳分开交付：

- **分析 worker/core**：Python 3.12（构建环境固定，运行时随 worker 提供）、NumPy/SciPy、Pillow；Matplotlib 的非交互 Agg/SVG/PDF 渲染或等价专用报告绘图实现仅放在绘图/报告模块；
- **Windows UI**：WPF/.NET 8，使用 self-contained `win-x64` 发布，外壳通过 NDJSON worker interface 调用 Python worker；不在 C# 重写测量算法；
- **worker/CLI 冻结**：先以 PyInstaller one-folder 作为发行物实现；若依赖烟测证明 Nuitka standalone 更适合，再替换交付 adapter，不改变 worker interface。

WPF/.NET self-contained 发布会把 .NET 运行时随应用带上；Python worker 的依赖和 C/C++ runtime 也必须由发行物明确提供或在目标 Windows 基线中验证。构建环境锁定依赖版本、架构（MVP 为 win-x64）和算法 profile；构建后在没有 Python、.NET SDK、编译器或开发工具的干净 Windows 环境执行启动、解码、分析、取消和导出烟测。所有 Python/.NET/第三方 notices 和许可证文本随发行物提供；许可证审查不能被打包成功替代。

交付分两档：

1. **主档：one-folder portable ZIP**。包含 WPF self-contained 外壳和 worker 目录，解压到用户有写权限的目录即可运行；分析输入只读，报告写入用户选择的独立目录。
2. **可选档：per-user installer**。安装到 `%LocalAppData%\\SpotAnalyzer`，不写入 `Program Files`、系统 PATH 或机器级注册表，不要求管理员权限；卸载也限定在用户范围。

优先使用 one-folder 而非 single-file：Python scientific DLL、worker 依赖和 .NET native files 更容易审查，启动时不会把整个发行物隐式解包到临时目录；single-file 只作为后续有明确启动/杀软验收证据时的便利包装。PyInstaller 和 `dotnet publish` 只属于交付 adapter，不能改变核心或 worker interface。每个发行物保存构建 manifest、依赖版本、算法版本和文件哈希；便携包和安装包必须运行同一套核心回归测试。

### 交付事实依据

以下是实现交接时应复核的官方文档；它们支持“目标机不预装开发环境”这一交付判断，但不替代干净 Windows 烟测和许可证审查：

- [.NET deployment](https://learn.microsoft.com/en-us/dotnet/core/deploying/) 与 [`dotnet publish`](https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-publish)：self-contained 发布随应用携带 .NET runtime；single-file 仍有 native 文件/解压和启动代价。
- [PyInstaller operating modes](https://pyinstaller.org/en/stable/operating-mode.html)：one-folder 先于 one-file 验证；one-file 会在启动时解压到临时目录，可能带来启动、残留和安全审查问题。
- [Python embeddable package](https://docs.python.org/3/using/windows.html#the-embeddable-package)：若不使用冻结工具，Python 运行时也可以 app-local 提供，但依赖必须随应用 vendor，不能把目标机 pip 当作部署方案。
- [Microsoft installer per-user/per-machine](https://learn.microsoft.com/en-us/windows/win32/msi/allusers)：安装范围决定是否需要管理员权限；portable ZIP 仍是首选无安装路径。

## 为什么不选其他主方案

- **纯 .NET/WPF/WinUI + 重写算法**：Windows UI 很自然，但会把 NumPy/SciPy 生态中的预处理、拟合和科学验证重新实现一遍，增加数值漂移和双实现风险。因此采用 WPF 外壳，但不采用全 .NET 科学核心。
- **WebView2 UI + 本地 HTTP/RPC 核心**：可以复用 web UI，但新增运行时版本、进程生命周期、端口/协议和资源加载问题；离线无管理员交付并没有因此更简单。现有 HTML 只是 Issue #5 的信息架构原型，不是要求把生产 UI 做成 web 应用。
- **PySide6 单进程桌面应用**：能减少协议代码，但把 Python/数值库/Qt 运行时和 UI 合成同一故障域，并引入 Qt 共享库的许可证与替换交付责任。若后续证明 WPF worker 维护成本过高，可作为实现替代，但不改变核心/worker interface。
- **Python 核心直接嵌入 WPF**：会把 Python 生命周期、线程、崩溃和原生 DLL 装载暴露给 UI；worker 分进程以少量启动和序列化成本换取取消与故障隔离。
- **插件式多模型架构**：MVP 只有一个经验证的主模型。现在暴露插件注册接口会把尚未冻结的模型选择和版本兼容复杂度泄露给 UI；第二个真实 adapter 出现时再抽取扩展 seam。

## 验收与交接

1. 核心测试用内存 `InputImage` 和 Issue #4 的合成 manifest 覆盖圆/旋转椭圆、多峰、环、梯度、坏点、低 SNR、饱和和裁边；不需要启动 Qt。
2. 同一输入与完整分析配置重复运行时，指标、状态、原因码、掩膜统计和派生视图一致；记录 ID/生成时间可不同。
3. CLI 和 UI 的同一配置快照得到相同 `AnalysisRecord` 内容；UI 的显示拉伸、色图和叠加层不改变它。
4. 输入语义不明、标定未确认、ROI 未确认、上游配置改变、质量门控失败和导出失败都显示结构化状态，不以异常或空白数值掩盖原因。
5. 干净 Windows 环境可从 portable ZIP 启动并完成一次分析与 PDF/PNG 导出；无需管理员权限、Python、Qt 或编译器。
6. 报告测试检查 `record_id`、用户报告名称、时间后缀策略、同名不覆盖、输入只读和门控后的 `null`/N/A；不以 PDF 二进制字节完全相等作为唯一断言。

## 相关领域语言

本设计使用 `CONTEXT.md` 中的“输入图像”“校正强度图”“显示图像”“空间标定”“分析区域”“分析模型”“分析配置”“分析记录”“计算成功”“测量有效性”“测量报告”和“报告名称”。“计算成功”只描述流程完成，不替代“测量有效性”；“显示图像”永远不是指标数据源。
