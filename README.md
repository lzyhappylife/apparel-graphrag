# 衣证 · 服装产品证据图谱与供应链决策系统

这是一个面向品牌、供应商、OEM/ODM 和出口企业的本地高精度 GraphRAG 工作台。它把服装产品、材料、供应商、工厂、批次、检测报告、认证证书、订单、质量记录和目标市场要求连接成可追溯证据图，并返回带原文定位的答案。

当前交付包含一个可运行的脱敏演示资料包，主题覆盖：

- 产品溯源与数字产品护照（DPP）
- 材料、纱线、面料批次和供应商关系
- REACH 纺织品化学品限制与检测证据
- OECD 服装供应链尽职调查
- Tech Pack、BOM、洗标和规格变更影响
- 订单、生产工厂、质量缺陷和检验记录

## 立即使用

当前电脑的模型文件已经随工作目录准备好。进入本目录执行：

```sh
./start.command
```

然后打开 `http://127.0.0.1:8765`。可以先试：

- XJ-2401 的再生材料声明能否追溯到证书、面料批次、检测报告和生产工厂？
- 如何把 REACH、化学品、检测报告和面料批次关联起来判断产品合规？
- 面料规格变化后哪些 Tech Pack、洗标、订单和质量文件需要检查？
- 没有订单和检验记录，能否证明某工厂昨天发生了色差？

`A6` 是脱敏演示资料，不代表真实企业、订单或第三方认证结果。生产使用时，应替换成企业原始文件并重新核验。

## 系统工作方式

1. 导入 Tech Pack、BOM、检测报告、认证证书、SDS、订单、审计和质量记录。
2. 人工确认来源、版本、适用市场和资料类型。
3. 本地模型提出实体关系候选，审核通过后才进入业务图。
4. BM25 与中文向量并行召回，再沿已审核关系扩展并用交叉编码器重排。
5. Agentic 模式把复杂问题拆成最多 4 个子问题，最多补检索 2 个缺口，最终再次检查证据覆盖。
6. 答案逐句引用原文；数字、批次、条件和适用范围不在证据中时拒答或保留原文摘录。

## 图谱核心实体

`SKU / 款式 / BOM / Tech Pack / 面料 / 纱线 / 批次 / 供应商 / 工厂 / 检测报告 / 认证证书 / SDS / 订单 / 质量记录 / 目标市场 / REACH / 数字产品护照`

## 可配置的通用结构

领域配置保存在 `data/domain-profile.json` 和 `data/schemas.json`，模型角色配置保存在 `data/model-config.json`。替换成其他行业时，需要同时替换：

- 实体类型、关系标签、同义词和适用范围规则
- 文档接入和解析方式
- 领域证据审核提示词
- 领域评测题和允许证据集合

模型可以按角色自由替换：规划器、关系抽取器、证据验证器、回答整理器、向量模型和重排模型。更换回答或验证模型后可直接重跑问答；更换向量模型要重建索引；更换抽取模型要重新抽取并复核关系。每次问答应记录领域配置、模型配置和索引版本。

## 运行边界

系统绑定本机回环地址，面向个人本地使用。没有证据时拒答，不把行业规范当成某个订单已经发生的事实。欧盟、REACH 或其他市场要求的正式结论，必须回看原始链接并确认目标市场和最新版本。

## 当前资料来源

| 来源 | 用途 |
|---|---|
| [European Commission Digital Product Passport](https://single-market-economy.ec.europa.eu/single-market/digital-product-passport_en) | 产品护照、来源、材料、可维修性、循环利用和纺织服装优先品类 |
| [Regulation (EU) 2024/1781](https://eur-lex.europa.eu/eli/reg/2024/1781/oj/eng) | DPP 数据元素、合规文件、技术文件和设施标识 |
| [ECHA REACH Annex XVII](https://echa.europa.eu/documents/10162/bd51087b-9917-b018-69ac-d7594315e2a9) | 纺织品、皮革和着色相关物质限制 |
| [OECD Responsible garment and footwear supply chains](https://www.oecd.org/en/topics/sub-issues/due-diligence-guidance-for-responsible-business-conduct/responsible-garment-and-footwear-supply-chains.html) | 多层级供应链尽调和风险治理 |

官方页面要点在本地以可检索摘要保存，正式合规使用须回看链接中的最新原文。

## 本地模型

- `BAAI/bge-small-zh-v1.5`：中文向量，512 维，本地 CPU。
- `BAAI/bge-reranker-v2-m3-int8`：交叉编码重排，本地 CPU。
- `Qwen3-4B-Instruct-2507-4bit`：关系抽取、证据验证、Agentic 规划、缺口补检索、回答整理和逐句审计，本地 MLX。
- macOS Apple Vision OCR：扫描 PDF 的系统 OCR，不是大语言模型。

## 目录

- `server.py`：本地 HTTP API 和工作台服务
- `engine.py`：文档、条款、实体、关系、索引和查询
- `retrieval.py`：混合召回、图扩展、适用范围和拒答
- `agentic.py`：有界 Agentic 规划和缺口补检索
- `relations.py`、`extraction_worker.py`：关系候选和审核生命周期
- `data/domain-profile.json`：行业配置
- `data/schemas.json`：实体、关系和同义词
- `data/sources.json`：演示资料和官方摘要
- `data/extra-relations.json`：已审核业务关系
- `data/evidence-benchmark.json`：服装场景基准题
