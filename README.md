# 烛龙 · Zhulong Search Protocol

让联网研究留下可复核的证据、明确的未知和有边界的结论。

烛龙是一套给 AI 助手和研究者使用的搜索协议，适用于多来源调研、选型、数据比较和背景核查。它规定如何定题、取证、比较和停手；不会替你提供搜索引擎或核验事实。单点事实查询通常不需要整套流程。

**协议 v1.6 · 首次公开发行 v1.6.0 · MIT**

## 开始使用

1. 阅读 [协议核心](docs/zhulong/SOP.md)，按题目打开相关细则和预设。
2. 复制 [研究记录模板](templates/research.md) 到自己的研究目录。短题一份 Markdown 即可；占位模板本身不会通过检查。
3. 用你已有的联网工具执行研究，保留原文短引、来源、时点与读取限制。对关键结论回读原文。
4. 完成人工自查；若有 Python 环境，可运行随附的结构检查器。

可向助手这样提出任务：

> 按本项目的烛龙协议，研究［问题］。我的硬约束是［条件］，决策时点是［日期］。先明确预算，再查来源。结论回指证据，未知项保持未知。

跨领域题可组合预设，共用一个总预算。没有历史记录即可开始；缺少独立召回种子时如实说明，不倒填“事前登记”。

## 内容导航

| 内容 | 用途 |
|---|---|
| [SOP](docs/zhulong/SOP.md) | 七步研究流程与停止条件 |
| [证据与交付](docs/zhulong/references/evidence-and-delivery.md) | 单文件记录、证据字段、自查 |
| [发现与预算](docs/zhulong/references/discovery-and-budget.md) | 覆盖、访问失败、召回与停手 |
| [证据关系](docs/zhulong/references/relations.md) | 对象、单位、时点、比较与推导 |
| [维护与验证](docs/zhulong/references/maintenance.md) | 修改规则与评估方法 |
| [学术](docs/zhulong/presets/P-001.md) · [新闻](docs/zhulong/presets/P-002.md) · [排障](docs/zhulong/presets/P-003.md) | 领域预设 |
| [数据与计费](docs/zhulong/presets/P-004.md) · [选型](docs/zhulong/presets/P-005.md) · [背景核查](docs/zhulong/presets/P-006.md) | 领域预设 |
| [完整合成示例](examples/minimal/00-research.md) | 观察记录如何组织；不是现实研究结论 |

## 结构检查器

Python 3.10+；运行时仅用标准库。命令在仓库根目录执行。

```sh
python scripts/zhulong/dossier_check.py examples/minimal --strict
python scripts/zhulong/dossier_check.py path/to/research --strict --json
python scripts/zhulong/dossier_check.py --self-check
```

`--self-check` 检查随包的合成正反样例。它会显示反例 FAIL，再确认该错误被正确识别；命令退出 0 表示这些预期对照成立。

| 输出 | 含义 |
|---|---|
| PASS | 已实现的结构和启发式检查没有发现问题 |
| WARN | 需要人工处置的提示或显式跳过项 |
| FAIL | 检查发现结构问题、规则命中或输入/依赖读取失败 |

普通审计模式只报告；`--strict` 遇 FAIL 退出 1，WARN 仍可退出 0。需要警告也拦截时用 `--fail-on-warn`。参数或输入目录错误退出 2。`--self-check` 单独运行，不与输出格式、严格模式或登记簿选项组合。

**PASS 和退出码 0 都不证明事实正确。** 详细范围和局限见 [检查器说明](docs/checker.md)。

### 易变事实登记

随包 [登记簿](docs/zhulong/volatile-facts-registry.md) 是空模板，没有内置来源黑名单。将它复制到自己的项目，保存价格、版本与条款等事实，再传入路径：

```sh
python scripts/zhulong/dossier_check.py path/to/research --registry path/to/registry.md --strict
```

未配置个人登记簿时，默认使用随包空模板，并显示规则数。明确暂不检查登记簿可传 `--no-registry`，结果会有 WARN。缺失、不可读或损坏的登记簿不会被当作有效空规则。价格类研究还检查目录名是否已登记；这只是关联提示，不核验登记值真伪。

## 作为技能使用

[skills/zhulong](skills/zhulong/SKILL.md) 是完整可携目录，包含核心与配套细则。可以把整个目录放到支持 `SKILL.md` 的工具所指定的技能位置；只复制入口文件会丢失依赖。

也可用脚本生成、检查或安装到你选择的位置：

```sh
python scripts/zhulong/sync_skill.py
python scripts/zhulong/sync_skill.py --check
python scripts/zhulong/sync_skill.py --target path/to/skills/zhulong
```

默认仅生成仓库内的 `skills/zhulong`。外部目标已有不同内容时会停止；确认更新可加 `--backup-existing`，先把旧的整个 `zhulong` 目录备份到同级，再安装。备份保留用户自有文件；脚本不会删除备份。安装后对同一 `--target` 加 `--check` 验证。

备份仍含旧的 `SKILL.md`。若宿主会扫描同级所有技能目录，应将备份移到技能扫描目录之外保存，避免同时发现新旧两份。

技能提供研究指令，不替宿主工具接通网络，也不保证每个宿主自动发现技能。若只使用技能文本而没有本仓库与 Python，执行人工自查并说明没有运行结构检查器。

## 开发与验证

```sh
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python scripts/zhulong/sync_skill.py --check
```

开发测试包含输入损坏、登记簿丢失、规则缓存、CLI 退出码、证据编号冲突、模块链接与安装备份。真实研究方法对照与代码测试分开看：一次答卷更好不能证明总体有效，结构测试通过也不能证明搜索充分。发布前对照的可公开摘要见 [评估说明](docs/evaluation.md)。

## 许可

本仓库代码与原创文档采用 [MIT](LICENSE)。引用的外部网站与第三方内容遵循其各自许可。
