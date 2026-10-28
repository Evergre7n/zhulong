"""烛龙量具的固定边界对照；不依赖真实 dossier 内容或联网。"""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/zhulong/dossier_check.py"
spec = importlib.util.spec_from_file_location("zhulong_dossier_check_tested", SCRIPT)
checker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checker
spec.loader.exec_module(checker)


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    registry = tmp_path / "registry.md"
    registry.write_text("", encoding="utf-8")
    monkeypatch.setattr(checker, "VOLATILE_REGISTRY", registry)
    monkeypatch.setattr(checker, "_REGISTRY_CACHE", None)


def dossier(tmp_path, *, version="1.5", query=None, rows=None, status="已关闭", answers=None, name="sample"):
    path = tmp_path / ("2026-10-26-" + name)
    path.mkdir()
    query = query if query is not None else '- "厂商 官方 技术说明"'
    rows = rows if rows is not None else "| E01 | https://example.invalid/doc | 全文 | 第一方 | 单源 | 2026-10-26 |"
    answers = answers if answers is not None else "1. 口径对表：按立案时间窗口核对，未跨期。\n2. 同物同值：E01 在正文与台账取值一致。\n3. 补查传到：本轮没有补查修订件。"
    # 单文件多章节是正式支持形状，不依赖八个文件名。
    text = f"""# 立案卡
烛龙预设 P-002 · 协议 v{version}
读法：按已发布说明核验。关键假设：当前版本有效。
归档：2026-10-26T00:00Z
## 查询词登记
{query}
# 覆盖表
| 面 | 状态 |
|---|---|
| 官方 | {status} |
# 证据台账
| ID | URL | 读深 | 来源类型 | 信息状态 | 日期 |
|---|---|---|---|---|---|
{rows}
# 未知桶
目前未发现其他空白。
# 碰壁记录
本轮没有通道故障。
# 待开清单
本轮没有待开项目。
# 结论
已找到所需说明，见 E01。
# 自查
## 交账前三问
{answers}
"""
    (path / "00-交账.md").write_text(text, encoding="utf-8")
    return path


def inspect(path):
    return checker.run([path])[0]


def levels(result, check):
    return [f.level for f in result.findings if f.check == check]


def test_valid_single_document_passes(tmp_path):
    assert inspect(dossier(tmp_path)).status == "PASS"


@pytest.mark.parametrize("version, expected", [("1.5", ["WARN"]), ("1.6", [])])
def test_probability_word_does_not_force_uncalibrated_numbers_in_v16(tmp_path, version, expected):
    path = dossier(tmp_path, version=version)
    file = path / "00-交账.md"
    file.write_text(file.read_text(encoding="utf-8").replace("已找到所需说明，见 E01。", "较可能在后续版本提供，但没有可校准的概率数据。"), encoding="utf-8")
    assert levels(inspect(path), "结论表达") == expected


@pytest.mark.parametrize("version, expected", [("1.5", "FAIL"), ("1.4", "WARN")])
def test_empty_query_heading_is_not_a_log(tmp_path, version, expected):
    result = inspect(dossier(tmp_path, query="", version=version))
    assert levels(result, "查询登记") == [expected]


@pytest.mark.parametrize("rows", ["", "| | | | | | |", "| E01 | — | 全文 | 第一方 | 单源 | 2026-10-26 |"])
def test_no_valid_evidence_cannot_pass(tmp_path, rows):
    result = inspect(dossier(tmp_path, rows=rows))
    assert levels(result, "台账有效行") == ["FAIL"]


@pytest.mark.parametrize("status", ["未关闭，尚未完成", "尚未完成", "未完成", "未关闭 ✅"])
def test_negated_completion_stays_open(tmp_path, status):
    assert levels(inspect(dossier(tmp_path, status=status)), "闸5") == ["FAIL"]


def test_old_negated_completion_warns(tmp_path):
    assert levels(inspect(dossier(tmp_path, status="未关闭，尚未完成", version="1.4")), "闸5") == ["WARN"]


@pytest.mark.parametrize("answers", ["", "1. 口径对表\n2. 同物同值\n3. 补查传到", "1. 口径对表：✅\n2. 同物同值：通过\n3. 补查传到：待填", "1. 口径对表：本次已逐行核对。\n2. 同物同值：两个文件同值。"])
def test_three_questions_require_separate_answers(tmp_path, answers):
    assert levels(inspect(dossier(tmp_path, answers=answers)), "交账三问") == ["FAIL"]


def test_circled_heading_answers_are_supported(tmp_path):
    answers = "### ① 口径对表\n立案与结果口径一致。\n### ② 同物同值\nE01 与结论取值一致。\n### ③ 补查传到\n本轮没有补查件。"
    assert inspect(dossier(tmp_path, answers=answers)).status == "PASS"


@pytest.mark.parametrize("depth, source, state", [("摘要", "第一方", "独立双源证实"), ("全文", "聚合", "待核"), ("全文", "用户口径", "未知"), ("全文", "聚合", "非独立双源证实")])
def test_unqualified_evidence_needs_nobear_mark(tmp_path, depth, source, state):
    rows = f"| E01 | https://example.invalid | {depth} | {source} | {state} | 2026-10-26 |"
    assert levels(inspect(dossier(tmp_path, rows=rows)), "过时来源未标") == ["FAIL"]


@pytest.mark.parametrize("state", ["独立双源证实", "待核 〔不得承重〕", "待核 〔承重例外：第一方原文已复核〕"])
def test_qualified_or_marked_aggregate_allowed(tmp_path, state):
    rows = f"| E01 | https://example.invalid | 全文 | 聚合 | {state} | 2026-10-26 |"
    assert levels(inspect(dossier(tmp_path, rows=rows)), "过时来源未标") == []


def test_bad_date_fails_one_row_and_continues_batch(tmp_path):
    rows = "| E01 | https://example.invalid ￥100 | 全文 | 第一方 | 单源 | 2026-99-99 |"
    bad = dossier(tmp_path, rows=rows, name="bad")
    file = bad / "00-交账.md"
    file.write_text(file.read_text(encoding="utf-8").replace("已找到所需说明，见 E01。", "甲比乙贵 ￥100。"), encoding="utf-8")
    good = dossier(tmp_path, name="good")
    results = checker.run([bad, good])
    assert levels(results[0], "台账日期") == ["FAIL"]
    assert "2026-99-99" in next(f.msg for f in results[0].findings if f.check == "台账日期")
    assert results[1].status == "PASS"


def test_no_actual_self_check_is_failure():
    ok, lines = checker.self_check([])
    assert not ok
    assert any("无实际执行" in line for line in lines)


@pytest.mark.parametrize("strict, expected", [(False, 0), (True, 2)])
def test_self_check_rejects_strict_combination(tmp_path, monkeypatch, strict, expected):
    path = dossier(tmp_path, query="")
    monkeypatch.setattr(checker, "SELF_CHECK", [(path.name, "查询登记", "FAIL")])
    monkeypatch.setattr(checker, "SELF_CHECK_PASS", [])
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), str(path), "--self-check"] + (["--strict"] if strict else []))
    if strict:
        with pytest.raises(SystemExit) as exc:
            checker.main()
        assert exc.value.code == expected
    else:
        assert checker.main() == expected


def test_declared_historical_version_preserved(tmp_path):
    result = inspect(dossier(tmp_path, version="0.2", query="", rows=""))
    assert result.version == (0, 2)
    assert result.status == "WARN"


@pytest.mark.parametrize("query", ["| 查询词 | 通道 |\n|---|---|", "### 查询细目\n", "TODO"])
def test_query_headings_and_empty_table_are_not_content(tmp_path, query):
    assert levels(inspect(dossier(tmp_path, query=query)), "查询登记") == ["FAIL"]


@pytest.mark.parametrize("status, expected", [("已关（③ 全域未饱和，裁定面饱和）", []), ("未关闭，已传待开清单", ["WARN"])])
def test_disclosed_saturation_and_open_disposition_supported(tmp_path, status, expected):
    assert levels(inspect(dossier(tmp_path, status=status)), "闸5") == expected


def test_local_experimental_objects_are_valid_evidence(tmp_path):
    rows = "| E01 | git show abc123（官方 clone 对象） | 全文 | 本机实验 | 已核 | 2026-10-26 |"
    assert inspect(dossier(tmp_path, rows=rows)).status == "PASS"


def test_new_mark_rule_warns_for_historical_version(tmp_path):
    rows = "| E01 | https://example.invalid | 摘要 | 聚合 | 待核 | 2026-10-26 |"
    assert levels(inspect(dossier(tmp_path, version="1.4", rows=rows)), "过时来源未标") == ["WARN"]


def test_marked_value_still_cannot_enter_comparison(tmp_path):
    rows = "| E01 | https://example.invalid | 摘要 | 聚合 | 待核 〔不得承重〕 | 2026-10-26 |"
    path = dossier(tmp_path, rows=rows)
    file = path / "00-交账.md"
    file.write_text(file.read_text(encoding="utf-8").replace("已找到所需说明，见 E01。", "E01 显示甲领先两倍。"), encoding="utf-8")
    assert levels(inspect(path), "不得承重进关系句") == ["FAIL"]


@pytest.mark.parametrize("filename", ["00-交账.md", "研究记录.md"])
def test_role_headings_enforce_gate_relation_recall_and_registry(tmp_path, filename):
    rows = "| E01 | https://example.invalid ￥100 | 摘要 | 聚合 | 待核 〔不得承重〕 | 2026-10-26 |"
    path = dossier(tmp_path, version="1.6", status="未关闭，尚未完成", rows=rows)
    original = path / "00-交账.md"
    text = original.read_text(encoding="utf-8").replace("读法：", "议题：选型比较现价。\n读法：")
    text = text.replace("已找到所需说明，见 E01。", "E01 显示甲领先两倍。")
    original.unlink()
    (path / filename).write_text(text, encoding="utf-8")
    result = inspect(path)
    for check in ["闸5", "不得承重进关系句", "召回探针", "易变登记"]:
        assert levels(result, check) == ["FAIL"]


@pytest.mark.parametrize("filename", ["00-交账.md", "研究记录.md"])
def test_ledger_descendants_are_not_relationship_conclusions(tmp_path, filename):
    rows = "| E01 | https://example.invalid ￥100 | 摘要 | 聚合 | 待核 〔不得承重〕 | 2026-10-26 |"
    path = dossier(tmp_path, rows=rows)
    original = path / "00-交账.md"
    text = original.read_text(encoding="utf-8").replace("# 未知桶", "## 原始数值\nE01 的原文称甲领先两倍。\n# 未知桶")
    original.unlink()
    (path / filename).write_text(text, encoding="utf-8")
    result = inspect(path)
    assert levels(result, "不得承重进关系句") == []
    assert result.status == "PASS"


@pytest.mark.parametrize("filename", ["00-交账.md", "研究记录.md"])
def test_product_descendants_inherit_relationship_role(tmp_path, filename):
    rows = "| E01 | https://example.invalid | 摘要 | 聚合 | 待核 〔不得承重〕 | 2026-10-26 |"
    path = dossier(tmp_path, rows=rows)
    original = path / "00-交账.md"
    text = original.read_text(encoding="utf-8").replace("# 结论\n", "# 结论\n## 证据关系说明\n### 实际推荐\n")
    text = text.replace("已找到所需说明，见 E01。", "E01 显示甲领先两倍。")
    original.unlink()
    (path / filename).write_text(text, encoding="utf-8")
    assert levels(inspect(path), "不得承重进关系句") == ["FAIL"]


def test_card_scope_does_not_include_sibling_waiting_list(tmp_path):
    path = dossier(tmp_path)
    file = path / "00-交账.md"
    text = file.read_text(encoding="utf-8").replace("# 待开清单\n", "# 待开清单\n价格比较留待其他任务。\n")
    file.write_text(text, encoding="utf-8")
    result = inspect(path)
    assert levels(result, "召回探针") == []
    assert levels(result, "易变登记") == []


def test_six_level_headings_declare_roles(tmp_path):
    path = dossier(tmp_path)
    file = path / "00-交账.md"
    text = file.read_text(encoding="utf-8").replace("# 结论", "###### 结论")
    file.rename(path / "研究记录.md")
    (path / "研究记录.md").write_text(text.replace("已找到所需说明，见 E01。", "E01 显示甲领先两倍。"), encoding="utf-8")
    result = checker.load(path)
    assert "研究记录.md" in checker.role_files(result, checker.PRODUCT_RE)


# 补丁回归：所有 dossier 与登记簿均为 tmp_path 下的合成数据。 All dossiers and registries are synthesized under tmp_path.
@pytest.mark.parametrize("answers", [
    "1. 八题硬约束均兑现，合同分母保持120。\n2. 关键结论回指来源，操作数与单位已列。\n3. 本轮无额外补查，缺口已传未知桶。",
    "① 立案与结论的口径一致。\n② E01与正文取值相同。\n③ 无补查修订。",
    "1、口径已核对，窗口相符。\n2、来源及操作数已记录。\n3、本次没有补查件。",
])
def test_complete_numbered_statements_do_not_require_colons(tmp_path, answers):
    assert levels(inspect(dossier(tmp_path, answers=answers)), "交账三问") == []


@pytest.mark.parametrize("answers", [
    "1. 是否已核对立案窗口并保持口径一致\n2. 操作数与来源是否已核对且相同\n3. 是否已完成所有补查传递",
    "1. 口径对表：是否已确认所有时间窗口一致？\n2. 同物同值：是否已确认各处取值相同？\n3. 补查传到：是否已完成传递？",
    "1. [ ]\n2. [x]\n3. ✅",
    "1. [ ] 口径对表\n2. [ ] 同物同值\n3. [ ] 补查传到",
    "1. 请逐项核对立案和正文的口径是否一致\n2. 确认所有来源及操作数已记录且取值相同。\n3. 检查补查已传到全部产物并完成修订。",
    "1. 是否已核对立案窗口并保持口径一致\n2. 已核对取值相同。\n3. 没有补查。",
])
def test_questions_and_empty_checkboxes_are_not_answers(tmp_path, answers):
    assert levels(inspect(dossier(tmp_path, answers=answers)), "交账三问") == ["FAIL"]


def revise_card(path, card):
    file = path / "00-交账.md"
    text = file.read_text(encoding="utf-8")
    file.write_text(text.replace("读法：按已发布说明核验。", card + "\n读法：按已发布说明核验。"), encoding="utf-8")


@pytest.mark.parametrize("version, expected", [("1.7", []), ("1.6", []), ("1.5", ["FAIL"]), ("1.4", ["FAIL"]), ("1.3", ["FAIL"])])
def test_general_data_p004_is_version_scoped(tmp_path, version, expected):
    path = dossier(tmp_path, version=version)
    revise_card(path, "预设 P-004，核对入组分母与通过率。")
    assert levels(inspect(path), "易变登记") == expected


@pytest.mark.parametrize("preset", ["P-004", "P-002"])
def test_actual_price_claim_still_requires_registry_in_v16(tmp_path, preset):
    path = dossier(tmp_path, version="1.6")
    revise_card(path, f"预设 {preset}，核验该产品当前售价与现价。")
    assert levels(inspect(path), "易变登记") == ["FAIL"]


def test_registered_actual_price_claim_passes(tmp_path):
    path = dossier(tmp_path, version="1.6")
    revise_card(path, "预设 P-004，核验当前价格。")
    checker.VOLATILE_REGISTRY.write_text(path.name, encoding="utf-8")
    assert levels(inspect(path), "易变登记") == []


@pytest.mark.parametrize("rid", ["D01-S1", "E01", "N02", "V003", "R12", "SRC.1", "`D01-S1`"])
def test_actual_ledger_ids_trigger_nobear_relation_check(tmp_path, rid):
    rows = f"| {rid} | https://example.invalid | 全文 | 第一方 | 单源 〔不得承重〕 | 2026-10-26 |"
    path = dossier(tmp_path, version="1.6", rows=rows)
    file = path / "00-交账.md"
    file.write_text(file.read_text(encoding="utf-8").replace("已找到所需说明，见 E01。", f"据{rid}，甲高于乙两倍。"), encoding="utf-8")
    assert levels(inspect(path), "不得承重进关系句") == ["FAIL"]


@pytest.mark.parametrize("reference", ["D01-S10", "XD01-S1", "D01-S1x", "D01-S1-extra", "D01-S1_suffix", "D01-S2"])
def test_ledger_id_substrings_and_uncited_rows_do_not_match(tmp_path, reference):
    rows = "| D01-S1 | https://example.invalid | 全文 | 第一方 | 单源 〔不得承重〕 | 2026-10-26 |"
    path = dossier(tmp_path, version="1.6", rows=rows)
    file = path / "00-交账.md"
    file.write_text(file.read_text(encoding="utf-8").replace("已找到所需说明，见 E01。", f"据{reference}，甲高于乙两倍。"), encoding="utf-8")
    assert levels(inspect(path), "不得承重进关系句") == []


def test_safe_d_source_is_allowed_in_relation(tmp_path):
    rows = "| D01-S1 | https://example.invalid | 全文 | 第一方 | 已核 | 2026-10-26 |"
    path = dossier(tmp_path, version="1.6", rows=rows)
    file = path / "00-交账.md"
    file.write_text(file.read_text(encoding="utf-8").replace("已找到所需说明，见 E01。", "D01-S1显示甲高于乙两倍。"), encoding="utf-8")
    assert levels(inspect(path), "不得承重进关系句") == []


def test_hypothesis_in_diagnostics_does_not_fill_case_card(tmp_path):
    path = dossier(tmp_path, version="1.6")
    file = path / "00-交账.md"
    text = file.read_text(encoding="utf-8").replace("关键假设：当前版本有效。", "")
    file.write_text(text + "\n# 体检诊断\n关键假设：当前版本有效。\n", encoding="utf-8")
    assert levels(inspect(path), "立案两项") == ["FAIL"]


def test_unknown_publication_date_keeps_warning(tmp_path):
    rows = "| D05-S3 | https://example.invalid | 访问失败记录 | 第一方 | blocked | 未知 |"
    result = inspect(dossier(tmp_path, version="1.6", rows=rows))
    assert levels(result, "台账行") == ["WARN"]


@pytest.mark.parametrize("rid, reference, expected", [
    ("S1", "S1", ["FAIL"]),
    ("S1", "S10", []),
    ("**", "无编号", []),
])
def test_short_and_empty_normalized_ids_do_not_match_substrings(tmp_path, rid, reference, expected):
    rows = f"| {rid} | https://example.invalid | 全文 | 第一方 | 单源 〔不得承重〕 | 2026-10-26 |"
    path = dossier(tmp_path, version="1.6", rows=rows)
    file = path / "00-交账.md"
    file.write_text(file.read_text(encoding="utf-8").replace("已找到所需说明，见 E01。", f"据{reference}，甲高于乙两倍。"), encoding="utf-8")
    assert levels(inspect(path), "不得承重进关系句") == expected
