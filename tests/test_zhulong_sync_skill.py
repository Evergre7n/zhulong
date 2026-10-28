"""确认按需模块在可携技能中可读，避免只同步短核心而丢失正文。"""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('zhulong_sync', Path(__file__).resolve().parents[1] / 'scripts/zhulong/sync_skill.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def source(tmp_path):
    root = tmp_path / 'source'
    (root / 'references').mkdir(parents=True)
    (root / 'presets').mkdir()
    (root / 'history').mkdir()
    (root / 'SOP.md').write_text('# v1.6\n> **正本 = 本文件**\n[证据](references/evidence.md)\n', encoding='utf-8')
    (root / 'references/evidence.md').write_text('证据正文', encoding='utf-8')
    (root / 'presets/P-001.md').write_text('按题读取', encoding='utf-8')
    (root / 'history/SOP-v1.5.md').write_text('历史归档', encoding='utf-8')
    return root / 'SOP.md'


def test_modules_are_mirrored_and_missing_or_changed_is_detected(tmp_path):
    bundle = sync.build_bundle(source(tmp_path))
    assert 'references/evidence.md' in bundle and 'presets/P-001.md' in bundle
    assert 'history/SOP-v1.5.md' in bundle
    target = tmp_path / 'skill'
    for name, data in bundle.items():
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    assert sync.check_bundle(bundle, target) == []
    (target / 'references/evidence.md').write_text('漂移', encoding='utf-8')
    (target / 'SOP-from-repo.md').unlink()
    assert len(sync.check_bundle(bundle, target)) == 2


def test_dangling_core_pointer_is_rejected(tmp_path):
    sop = source(tmp_path)
    (sop.parent / 'references/evidence.md').unlink()
    with pytest.raises(ValueError, match='模块缺失'):
        sync.build_bundle(sop)


def test_real_core_links_resolve_and_raw_core_matches():
    bundle = sync.build_bundle()
    assert bundle['SOP-from-repo.md'] == sync.SOP.read_bytes()
    assert 'references/relations.md' in bundle


def test_dependency_between_modules_is_validated(tmp_path):
    sop = source(tmp_path)
    (sop.parent / 'references/evidence.md').write_text('[缺失](../presets/not-here.md)', encoding='utf-8')
    with pytest.raises(ValueError, match='not-here'):
        sync.build_bundle(sop)


def test_deleted_managed_module_is_not_silently_ignored(tmp_path):
    target = tmp_path / 'skill'
    target.mkdir()
    (target / sync.MANIFEST).write_text(json.dumps(['old.md']), encoding='utf-8')
    (target / 'old.md').write_text('过期规则', encoding='utf-8')
    assert sync.check_bundle({}, target) == [f'受管旧模块仍存在：{target / "old.md"}']
