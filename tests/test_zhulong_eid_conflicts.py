"""证据编号冲突诊断不改变原有关系层判定。"""
import pytest

from test_zhulong_dossier_check import checker, dossier, levels


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    registry = tmp_path / 'registry.md'
    registry.write_text('', encoding='utf-8')
    monkeypatch.setattr(checker, 'VOLATILE_REGISTRY', registry)
    monkeypatch.setattr(checker, '_REGISTRY_CACHE', None)


def ledger(rid, blocked):
    marker = '〔不得承重〕' if blocked else ''
    return f'| {rid} | https://example.invalid/doc | 全文 | 第一方 | 单源 {marker} | 2026-10-26 |'


def second_ledger(path, rows):
    (path / 'other.md').write_text(
        '# 证据台账\n| ID | URL | 读深 | 来源类型 | 信息状态 | 日期 |\n'
        '|---|---|---|---|---|---|\n' + '\n'.join(rows) + '\n', encoding='utf-8'
    )


@pytest.mark.parametrize('version,old_level', [('1.6', 'FAIL'), ('1.4', 'WARN')])
def test_conflicting_sources_warn_without_downgrading_relation(tmp_path, version, old_level):
    path = dossier(tmp_path, version=version, rows=ledger('E01', False))
    second_ledger(path, [ledger('E01', True)])
    output = path / '00-交账.md'
    output.write_text(output.read_text(encoding='utf-8').replace(
        '已找到所需说明，见 E01。', 'E01 显示甲高于乙两倍。'
    ), encoding='utf-8')
    d = checker.run([path])[0]
    assert levels(d, '不得承重进关系句') == [old_level]
    findings = [x for x in d.findings if x.check == '证据编号承重状态冲突']
    assert len(findings) == 1 and findings[0].level == 'WARN'
    assert '00-交账.md（未被现有规则判为不得承重）' in findings[0].msg
    assert 'other.md（不得承重）' in findings[0].msg


@pytest.mark.parametrize('rid,expected', [('E01', True), ('D01-S1', True), ('第一方', False), ('1', False)])
def test_diagnostic_identifier_subset(tmp_path, rid, expected):
    path = dossier(tmp_path, rows=ledger(rid, False))
    second_ledger(path, [ledger(rid, True)])
    d = checker.run([path])[0]
    assert bool(levels(d, '证据编号承重状态冲突')) is expected


def test_same_status_is_not_a_conflict(tmp_path):
    path = dossier(tmp_path, rows=ledger('E01', False))
    second_ledger(path, [ledger('E01', False)])
    assert levels(checker.run([path])[0], '证据编号承重状态冲突') == []


def test_legacy_seven_argument_constructor():
    assert checker.LedgerRow('E01', '', [], set(), [], False, False).file == '<未知文件>'
