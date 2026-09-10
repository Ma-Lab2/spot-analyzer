# Issue #10：RFC 8785 JSON canonicalization

- **研究日期**：2026-09-10
- **结论**：使用 PyPI `rfc8785==0.1.4` 作为 `analysis_fingerprint` 的 canonicalizer。该包提供 RFC 8785 / JSON Canonicalization Scheme 实现，`dumps(value)` 返回 canonical UTF-8 bytes，且为无依赖纯 Python 包，满足项目 Python 3.12 目标。

## 依据

1. [RFC 8785 — JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785.html) 定义了确定性的 JSON 表示，用于哈希和签名；要求使用 I-JSON 数据模型、ECMAScript 兼容的 primitive serialization，以及按规则排序的对象属性。
2. [PyPI rfc8785](https://pypi.org/project/rfc8785/) 记录版本 `0.1.4`、Python 3.8+ 支持、`rfc8785.dumps(value)` API、无运行时依赖和 Apache-2.0 许可。
3. [Trail of Bits rfc8785.py source](https://github.com/trailofbits/rfc8785.py) 是该实现的源代码仓库。

## 应用决策

- 依赖固定为 `rfc8785==0.1.4`。
- 输入先转换只读映射和元组为 JSON 可表示结构，再交给 `rfc8785.dumps`。
- canonicalizer 版本 `rfc8785-python-0.1.4` 写入 fingerprint 输入和分析诊断 provenance。
- 后续仍需在 Python 3.12 环境执行跨进程 golden vectors；本机 Python 3.10 测试不构成 Python 3.12 交付证据。
