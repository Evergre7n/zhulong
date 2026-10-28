"""公开包的输入失败、登记规则和安装安全边界。"""
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from test_zhulong_dossier_check import checker, dossier, levels
from test_zhulong_sync_skill import sync

ROOT = Path(__file__).resolve().parents[1]


def command(script, *args):
    return subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts/zhulong' / script), *map(str, args)],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=20,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )


@pytest.fixture(autouse=True)
def empty_registry(tmp_path, monkeypatch):
    p = tmp_path / 'rules.md'
    p.write_text('', encoding='utf-8')
    monkeypatch.setattr(checker, 'VOLATILE_REGISTRY', p)
    monkeypatch.setattr(checker, 'NO_REGISTRY', False)
    monkeypatch.setattr(checker, '_REGISTRY_CACHE', None)
    monkeypatch.setattr(checker, '_REGISTRY_ERROR', None)


def test_read_failure_is_fail_and_other_dossier_continues(tmp_path, monkeypatch):
    bad = dossier(tmp_path, name='bad')
    good = dossier(tmp_path, name='good')
    (bad / 'extra.md').write_text('evidence', encoding='utf-8')
    original = checker.read_text

    def guarded(path):
        if path == bad / 'extra.md':
            raise PermissionError('injected test failure')
        return original(path)

    monkeypatch.setattr(checker, 'read_text', guarded)
    results = checker.run([bad, good])
    assert levels(results[0], '输入读取') == ['FAIL']
    assert results[1].status == 'PASS'


def test_invalid_utf8_cannot_be_silently_replaced(tmp_path):
    p = dossier(tmp_path)
    with (p / '00-交账.md').open('ab') as f:
        f.write(b'\xff')
    result = command('dossier_check.py', p, '--strict', '--json')
    assert result.returncode == 1
    assert any(x['check'] == '输入读取' for x in json.loads(result.stdout)[0]['findings'])


@pytest.mark.parametrize('contents', [None, 'not a registry', '| # | 识别词 |\n|---|---|\n| S-001 | |\n'])
def test_missing_or_malformed_registry_never_passes(tmp_path, contents):
    p = dossier(tmp_path)
    registry = tmp_path / 'bad-registry.md'
    if contents is not None:
        registry.write_text(contents, encoding='utf-8')
    result = command('dossier_check.py', p, '--registry', registry, '--strict', '--json')
    assert result.returncode == 1
    doc = json.loads(result.stdout)[0]
    assert doc['registry']['error']
    assert any(x['check'] == '登记簿读取' for x in doc['findings'])


def test_registry_reloaded_between_runs(tmp_path):
    p = dossier(tmp_path, rows='| E01 | https://example.invalid/retired-source | 全文 | 第一方 | 单源 | 2026-10-26 |')
    assert checker.run([p])[0].status == 'PASS'
    checker.VOLATILE_REGISTRY.write_text('| # | 识别词 |\n|---|---|\n| S-001 | retired-source |\n', encoding='utf-8')
    assert levels(checker.run([p])[0], '过时来源未标') == ['FAIL']


def test_damaged_source_table_not_masked_by_valid_fact_table(tmp_path):
    p = dossier(tmp_path)
    checker.VOLATILE_REGISTRY.write_text(
        '| # | 识别词错 |\n|---|---|\n| S-001 | retired-source |\n\n'
        '| # | 状态 |\n|---|---|\n| V-001 | 现行 |\n', encoding='utf-8'
    )
    assert levels(checker.run([p])[0], '登记簿读取') == ['FAIL']


@pytest.mark.parametrize('broken', [
    '| # | 识别词 |\n|===|===|\n| S-001 | retired-source |\n',
    '| # | 识别词 |\n| S-001 | retired-source |\n',
    '# | 识别词 |\n|---|---|\n| S-001 | retired-source |\n',
])
def test_unparsed_registry_rows_cannot_disappear(tmp_path, broken):
    p = dossier(tmp_path)
    checker.VOLATILE_REGISTRY.write_text(
        broken + '\n| # | 状态 |\n|---|---|\n| V-001 | 现行 |\n', encoding='utf-8'
    )
    assert levels(checker.run([p])[0], '登记簿读取') == ['FAIL']


def test_registry_code_examples_are_not_active_rules(tmp_path):
    p = dossier(tmp_path)
    checker.VOLATILE_REGISTRY.write_text(
        '正文提及 S-001 不会登记规则。\n'
        '```markdown\n| # | 识别词 |\n|===|===|\n| S-001 | retired-source |\n```\n'
        '    | S-002 | indented-code |\n\n'
        '| # | 状态 |\n|---|---|\n| V-001 | 现行 |\n', encoding='utf-8'
    )
    assert levels(checker.run([p])[0], '登记簿读取') == []
    assert checker.load_registry_rules() == ([], {'V-001': '现行'})


def test_explicit_skip_is_visible_and_can_fail_on_warn(tmp_path):
    p = dossier(tmp_path)
    for flag, code in [('--strict', 0), ('--fail-on-warn', 1)]:
        result = command('dossier_check.py', p, '--no-registry', '--json', flag)
        assert result.returncode == code
        doc = json.loads(result.stdout)[0]
        assert doc['status'] == 'WARN' and doc['registry']['enabled'] is False


def test_cli_missing_path_and_file_are_usage_errors(tmp_path):
    f = tmp_path / 'file.md'
    f.write_text('not a directory', encoding='utf-8')
    for target in [tmp_path / 'absent', f]:
        result = command('dossier_check.py', target, '--strict')
        assert result.returncode == 2 and 'Traceback' not in result.stderr


def test_empty_default_root_is_not_a_success(tmp_path, monkeypatch):
    monkeypatch.setattr(checker, 'DOSSIER_ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['dossier_check.py', '--strict'])
    with pytest.raises(SystemExit) as exc:
        checker.main()
    assert exc.value.code == 2


def test_json_selfcheck_combination_rejected():
    result = command('dossier_check.py', '--json', '--self-check')
    assert result.returncode == 2


@pytest.mark.parametrize('flag', ['--strict', '--fail-on-warn', '--no-registry'])
def test_selfcheck_modes_do_not_contradict_the_exit_code(flag):
    result = command('dossier_check.py', '--self-check', flag)
    assert result.returncode == 2


def test_hardlinked_managed_file_cannot_overwrite_external_data(tmp_path):
    target = tmp_path / 'zhulong'
    target.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('keep outside data', encoding='utf-8')
    os.link(outside, target / 'SKILL.md')
    with pytest.raises(ValueError, match='硬链接'):
        sync.validate_target(target, {'SKILL.md'})
    assert outside.read_text(encoding='utf-8') == 'keep outside data'


@pytest.mark.parametrize('version, expected', [('1.6', ['WARN']), ('1.5', [])])
def test_missing_preregistration_honestly_disclosed(tmp_path, version, expected):
    p = dossier(tmp_path, version=version)
    f = p / '00-交账.md'
    text = f.read_text(encoding='utf-8').replace('读法：', '原题：选型。\n读法：')
    text += '\n召回探针：未预登记；原因：本题没有独立种子；相对召回：不可计算\n'
    f.write_text(text, encoding='utf-8')
    assert levels(checker.run([p])[0], '召回探针') == expected


@pytest.mark.parametrize('name', ['../outside.md', '/outside.md', 'C:/outside.md', '..\\outside.md', '.', ''])
def test_install_manifest_rejects_paths_outside_target(tmp_path, name):
    (tmp_path / sync.MANIFEST).write_text(json.dumps([name]), encoding='utf-8')
    with pytest.raises(ValueError):
        sync.managed_names(tmp_path)


def test_install_refuses_overwrite_then_preserves_entire_old_folder(tmp_path):
    target = tmp_path / 'zhulong'
    target.mkdir()
    (target / 'SKILL.md').write_text('old skill', encoding='utf-8')
    (target / 'my-notes.txt').write_text('keep me', encoding='utf-8')
    result = command('sync_skill.py', '--target', target)
    assert result.returncode == 2
    assert (target / 'SKILL.md').read_text(encoding='utf-8') == 'old skill'
    result = command('sync_skill.py', '--target', target, '--backup-existing')
    assert result.returncode == 0, result.stderr
    backups = list(tmp_path.glob('zhulong.backup-*'))
    assert len(backups) == 1
    assert (backups[0] / 'my-notes.txt').read_text(encoding='utf-8') == 'keep me'
    assert (backups[0] / 'SKILL.md').read_text(encoding='utf-8') == 'old skill'
    assert command('sync_skill.py', '--target', target, '--check').returncode == 0
