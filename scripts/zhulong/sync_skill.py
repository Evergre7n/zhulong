#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""烛龙协议副本同步（评估报告建议 9：版本号单一来源）。

正本 = 仓库 docs/zhulong/SOP.md；可携副本 = skills/zhulong/SKILL.md；安装位置由 --target 显式选择。
副本 = frontmatter + 常驻核心，按需细则和历史归档原样镜像到子目录。
SOP-from-repo.md 保持与核心正本逐字节相同；--check 同时核全部模块。

用法：
  python scripts/zhulong/sync_skill.py          从正本生成副本
  python scripts/zhulong/sync_skill.py --check  只核对，不写；有漂移退出码 1
"""

import argparse
import datetime
import uuid
import hashlib
import json
import posixpath
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOP = REPO / "docs" / "zhulong" / "SOP.md"
SKILL_DIR = REPO / "skills" / "zhulong"
SKILL = SKILL_DIR / "SKILL.md"
RAW_COPY = SKILL_DIR / "SOP-from-repo.md"
MANIFEST = ".zhulong-managed.json"

HEADER_MARK = "**正本 = 本文件**"
COPY_LINE = (
    "> **本文件是可携副本**，由 `scripts/zhulong/sync_skill.py` 从正本生成；正本 = 本项目的 "
    "`docs/zhulong/SOP.md`。改协议只改正本，再跑同步脚本覆盖本文件，不要直接改本文件；"
    "版本号以正本标题为准（单一来源）。"
)
FRONTMATTER = (
    "---\n"
    "name: zhulong\n"
    "description: 烛龙搜索协议：用于议题级联网调研、选型、枚举与背景核查；单点查询不套整套流程。先读短核心，按任务读取对应预设和证据细则。正本 docs/zhulong/SOP.md。\n"
    "---\n"
    "\n"
)


def build(sop=None):
    sop = sop or SOP
    raw = sop.read_bytes()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8")
    lines = text.split(nl)
    hits = [i for i, line in enumerate(lines) if HEADER_MARK in line]
    if len(hits) != 1:
        raise SystemExit(f"正本头部「{HEADER_MARK}」应恰好出现 1 次，实际 {len(hits)} 次——先修正本")
    lines[hits[0]] = COPY_LINE
    skill = FRONTMATTER.replace("\n", nl) + nl.join(lines)
    return raw, skill.encode("utf-8")


def build_bundle(sop=None):
    """每个活动模块只维护仓库一份；复制完整相对目录以便技能独立读取。"""
    sop = sop or SOP
    raw, skill = build(sop)
    bundle = {"SKILL.md": skill, "SOP-from-repo.md": raw}
    for folder in ("references", "presets", "history"):
        for path in sorted((sop.parent / folder).rglob("*.md")):
            bundle[path.relative_to(sop.parent).as_posix()] = path.read_bytes()
    # 检查所有活动模块的相对链接；历史归档里的旧路径不是新版本依赖。
    for name, content in bundle.items():
        if name.startswith("history/"):
            continue
        for link in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", content.decode("utf-8")):
            if re.match(r"^[a-zA-Z]+:", link) or link.startswith("/"):
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), link))
            if resolved not in bundle:
                raise ValueError(f"引用模块缺失：{name} → {link}")
    return bundle


def managed_names(target):
    manifest = target / MANIFEST
    if manifest.is_symlink():
        raise ValueError("受管清单不能是符号链接")
    if not manifest.exists():
        return set()
    names = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(names, list) or any(
        not isinstance(n, str) or not n or n == "." or ":" in n or "\\" in n
        or n.startswith("/") or ".." in n.split("/") for n in names
    ):
        raise ValueError("技能同步清单格式或路径非法")
    return set(names)


def validate_target(target, names):
    """只操作已选定目录内的普通文件；在写入/备份前检查所有路径。"""
    if target.is_symlink() or (hasattr(target, 'is_junction') and target.is_junction()):
        raise ValueError("目标目录不能是符号链接或联接点")
    resolved = target.resolve()
    if resolved == Path(resolved.anchor) or resolved in (REPO, SOP.parent):
        raise ValueError("不能把技能同步到根目录或协议源目录")
    for name in set(names) | {MANIFEST}:
        member = target / name
        if not member.resolve().is_relative_to(resolved):
            raise ValueError("受管路径越出目标目录")
        current = member
        while current != target:
            if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
                raise ValueError("受管路径不能包含链接")
            current = current.parent
        if member.exists() and not member.is_file():
            raise ValueError("受管文件位置被目录占用")
        if member.is_file() and member.stat().st_nlink > 1:
            raise ValueError("受管文件不能是硬链接")
    return resolved


def check_bundle(bundle, target):
    drift = [str(target / name) for name, content in bundle.items()
             if not (target / name).is_file() or (target / name).read_bytes() != content]
    drift.extend(f"受管旧模块仍存在：{target / name}" for name in managed_names(target) - bundle.keys()
                 if (target / name).exists())
    return drift


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="生成或安装烛龙可携技能")
    ap.add_argument("--check", action="store_true", help="只核对，不写")
    ap.add_argument("--target", type=Path, default=SKILL_DIR, help="目标目录；默认 skills/zhulong")
    ap.add_argument("--backup-existing", action="store_true", help="有差异时先将整个已有目标备份到同级目录，再安装")
    a = ap.parse_args()
    target = a.target.expanduser().absolute()
    try:
        bundle = build_bundle()
        previous = managed_names(target)
        resolved = validate_target(target, set(bundle) | previous)
        drift = check_bundle(bundle, target)
        if a.check:
            if drift:
                print("副本漂移：" + "；".join(drift))
                return 1
            print(f"副本一致（{len(bundle)} 件）")
            return 0
        if target.exists() and not target.is_dir():
            raise ValueError("目标不是目录")
        if target.exists() and any(target.iterdir()) and drift:
            if a.backup_existing:
                if target.name != "zhulong":
                    raise ValueError("整目录备份仅支持名为 zhulong 的技能目录")
                suffix = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
                backup = target.with_name(target.name + '.backup-' + suffix)
                if backup.exists() or backup.resolve().parent != resolved.parent:
                    raise ValueError("备份目标冲突或越界")
                # 已验证源与备份都位于用户明确选定目录的同一父目录。
                target.rename(backup)
                print(f"旧目录已备份：{backup}")
                previous = set()
            elif resolved != SKILL_DIR.resolve():
                raise ValueError("目标已有不同内容；请使用 --backup-existing 保留旧目录后安装")
        target.mkdir(parents=True, exist_ok=True)
        for name, content in bundle.items():
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        (target / MANIFEST).write_text(json.dumps(sorted(previous | bundle.keys()), ensure_ascii=False, indent=2), encoding="utf-8")
        leftovers = check_bundle(bundle, target)
        if leftovers:
            print("同步后仍需处置：" + "；".join(leftovers))
            return 1
        print(f"已同步 {len(bundle)} 件到 {target}")
        return 0
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"同步失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
