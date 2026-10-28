#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""烛龙 dossier 体检（只读量具，评估报告建议 1）。

按 docs/zhulong/SOP.md 的交账要求逐份检查 docs/zhulong/dossiers/ 下的 dossier，
只报告、不改任何文件。条款按 dossier 声明的协议版本分档生效：老 dossier 不追溯新条款。

用法：
  python scripts/zhulong/dossier_check.py                  体检全部 dossier
  python scripts/zhulong/dossier_check.py <dossier 目录>…   只查指定 dossier
  python scripts/zhulong/dossier_check.py --json           机读输出
  python scripts/zhulong/dossier_check.py --strict         有 FAIL 即退出码 1（新 dossier 交账前用）
  python scripts/zhulong/dossier_check.py --self-check     量具阳性对照：断言既有 dossier 的已知问题能被抓到

结果三档：FAIL = 违反该版本协议的硬要求；WARN = 可疑、需人看；PASS = 本量具没发现问题
（PASS 不等于内容正确——体检只管形状，不管结论真假）。
"""

import argparse
import datetime
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOSSIER_ROOT = REPO / "docs" / "zhulong" / "dossiers"
SPOT_LEDGER = REPO / "docs" / "zhulong" / "spot-check-ledger.md"
VOLATILE_REGISTRY = REPO / "docs" / "zhulong" / "volatile-facts-registry.md"
SPOT_EVERY = 5

# 未声明协议版本时按目录日期推断（10-15 当天从 v0.2 迭代到 v1.1，取最宽松的 v0.2）；
# v1.3 于 2026-10-22 落地，此后新建的 dossier 必须在立案卡声明版本，未声明按现行版查并报 FAIL
# v1.4（C-004「对照要验正文」）没有新的机械可查项，只跟着 SOP 标题升版本号
# v1.5（关系层，2026-10-25）：关系层四项对 v1.5 起的 dossier 报 FAIL，对更早的 dossier 只报 WARN（不追溯）
CURRENT_VERSION = (1, 6)
VERSION_BY_DATE = [
    ("2026-10-15", (0, 2)),
    ("2026-10-16", (1, 2)),
    ("2026-10-23", (1, 4)),
    ("2026-10-26", CURRENT_VERSION),
]

# ---------- 关键词 ----------
PART_KEYS = {
    "立案卡": ["立案"],
    "覆盖表": ["覆盖"],
    "证据台账": ["台账", "证据"],
    "未知桶": ["未知"],
    "碰壁记录": ["碰壁"],
    "待开清单": ["待开"],
}
PRODUCT_RE = re.compile(r"裁定|结论|交账|排序|汇总|主表|判断|杠杆|候选清单|对比矩阵|对标")
SUPPLEMENT_RE = re.compile(r"补查")
QUERY_HEADING_RE = re.compile(r"查询(词|登记|动作|明细|清单|序列|与请求)|检索式|query log", re.I)
QUERY_LINE_RE = re.compile(r"查询词|查询形状|已试查询|查询：|查询:|query", re.I)
QUERY_LABEL_RE = re.compile(r"查询词?登记|查询词明细|查询清单")
VERSION_RE = re.compile(r"(?:烛龙|搜索协议|协议版本|协议|SOP)[^\n]{0,12}?\bv(\d+)\.(\d+)\b")
QUOTED_RE = re.compile(r"`[^`\n]{2,}`|「[^」\n]{2,}」|“[^”\n]{2,}”|\"[^\"\n]{2,}\"")
UTC_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\s?Z\b"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\s?UTC\b(?!\s?[+\-−]\s?\d)"
)
DATE_RE = re.compile(r"\b(19|20)\d{2}(-\d{2}(-\d{2})?)?\b|\d{1,2}\s?月|\b(19|20)\d{2}\s?年")
VOLATILE_RE = re.compile(
    r"本机\s?Temp|%TEMP%|\\AppData\\Local\\Temp|AppData[\\/]Local[\\/]Temp|(?<![\w.])/tmp/", re.I
)
STATUS_HEADER_RE = re.compile(r"状态|终态")
COVERAGE_CTX_RE = re.compile(r"覆盖|矩阵")
INITIAL_CTX_RE = re.compile(r"执行前|初始|计划|预算")
CLOSED_RE = re.compile(r"已关|关格|通道坏|N\s?/\s?A|N-A|不适用|✅|closed|完成|饱和", re.I)
OPEN_RE = re.compile(r"未开|进行|部分|待补|待查|未关|open|todo|pending", re.I)
JUSTIFY_RE = re.compile(r"限流|429|传代|待开|N\s?/\s?A|不影响|停手|理由|披露|非必需|裁定面|[列见进传]\s?0?5\b")
URL_COL_RE = re.compile(r"URL|链接|出处|来源|对象|通道")
GRADE_COL_RE = re.compile(r"(^|[^等])级|档|读深")
EMPTY_CELL_RE = re.compile(r"^\s*(—|-|–|／|/|无)?\s*$")


@dataclass
class Table:
    file: str
    heading: str  # 所在位置的全部祖先标题，用「 / 」连接
    header: list
    rows: list


@dataclass
class Finding:
    check: str
    level: str  # FAIL / WARN
    msg: str


@dataclass
class Dossier:
    path: Path
    name: str
    date: str
    version: tuple
    version_declared: bool
    top_files: list = field(default_factory=list)
    all_files: list = field(default_factory=list)
    texts: dict = field(default_factory=dict)
    headings: list = field(default_factory=list)  # (file, level, text)
    tables: list = field(default_factory=list)
    findings: list = field(default_factory=list)

    def fail(self, check, msg):
        self.findings.append(Finding(check, "FAIL", msg))

    def warn(self, check, msg):
        self.findings.append(Finding(check, "WARN", msg))

    @property
    def status(self):
        levels = {f.level for f in self.findings}
        return "FAIL" if "FAIL" in levels else ("WARN" if "WARN" in levels else "PASS")


# ---------- 读取与解析 ----------
def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def parse_tables(fname: str, text: str):
    lines = text.split("\n")
    tables, stack, i = [], [], 0  # stack = [(级别, 标题)]，记祖先标题
    sep = re.compile(r"^\s*\|?[\s:\-|]+\|[\s:\-|]*$")
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            stack = [s for s in stack if s[0] < lvl] + [(lvl, m.group(2).strip())]
        if line.strip().startswith("|") and i + 1 < len(lines) and sep.match(lines[i + 1]) and "-" in lines[i + 1]:
            header = split_row(line)
            rows, j = [], i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(split_row(lines[j]))
                j += 1
            tables.append(Table(fname, " / ".join(s[1] for s in stack), header, rows))
            i = j
            continue
        i += 1
    return tables


def split_row(line: str):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    # 保护转义竖线
    s = s.replace("\\|", "\u0000")
    return [c.replace("\u0000", "|").strip() for c in s.split("|")]


def detect_version(texts: dict, top_files: list):
    """取立案卡（没有就取各文件）前 60 行里紧跟「烛龙 / 协议 / SOP」的版本号。"""
    order = [f for f in top_files if "立案" in f] + [f for f in top_files if "立案" not in f]
    for f in order:
        for line in texts[f].split("\n")[:60]:
            if "修订标记" in line:
                continue  # 事后加的修订标记写的是审查时的协议版本，不是该 dossier 执行时的版本
            m = VERSION_RE.search(line)
            if m:
                return (int(m.group(1)), int(m.group(2))), True
    return None, False


def version_from_date(date: str):
    v = (0, 2)
    for d, ver in VERSION_BY_DATE:
        if date >= d:
            v = ver
    return v


def load(path: Path) -> Dossier:
    name = path.name
    date = name[:10] if re.match(r"\d{4}-\d{2}-\d{2}", name) else "0000-00-00"
    errors, files = [], []
    if not path.is_dir():
        errors.append("指定路径不存在或不是目录")
    else:
        for folder, _, names in os.walk(path, onerror=lambda e: errors.append(str(e))):
            files.extend(Path(folder) / name for name in names if name.endswith(".md"))
    allf = sorted(str(f.relative_to(path)).replace("\\", "/") for f in files)
    top = [f for f in allf if "/" not in f]
    texts = {}
    for f in allf:
        try:
            texts[f] = read_text(path / f)
        except (OSError, UnicodeError) as exc:
            texts[f] = ""
            errors.append(f"{f}: {type(exc).__name__}")
    ver, declared = detect_version(texts, top)
    if ver is None:
        ver = version_from_date(date)
    d = Dossier(path, name, date, ver, declared, top, allf, texts)
    for error in errors:
        d.fail("输入读取", error)
    if not allf:
        d.fail("输入读取", "目录中没有可检查的 Markdown 文件")
    if ver > CURRENT_VERSION:
        d.warn("协议版本", "声明版本高于检查器支持版本，额外规则未经检查")
    for f in allf:
        for line in texts[f].split("\n"):
            m = re.match(r"^(#{1,6})\s+(.*)$", line)
            if m:
                d.headings.append((f, len(m.group(1)), m.group(2).strip()))
        d.tables.extend(parse_tables(f, texts[f]))
    return d


# ---------- 检查项 ----------
def at_least(d: Dossier, ver):
    return d.version >= ver


def shape_issue(d: Dossier, check: str, msg: str):
    """新增的结构校验仅对现役 v1.5 硬阻断，旧档案保留提示。"""
    (d.fail if at_least(d, (1, 5)) else d.warn)(check, msg)


def section_text(text: str, title_re):
    """按标题边界取段，支持单文件多章节 dossier。"""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        h = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not h or not title_re.search(h.group(2)):
            continue
        end = i + 1
        while end < len(lines):
            following = re.match(r"^(#{1,6})\s+", lines[end])
            if following and len(following.group(1)) <= len(h.group(1)):
                break
            end += 1
        yield "\n".join(lines[i + 1:end])


def role_files(d: Dossier, role_re):
    """文件名或任意层级标题声明角色，不能靠改文件名避开检查。"""
    return [f for f in d.top_files if role_re.search(f) or any(
        hf == f and role_re.search(title) for hf, _, title in d.headings)]


def role_text(d: Dossier, role_re):
    """有角色章节时只取该章；旧式专用文件无匹配标题时保留全文。"""
    parts = []
    for f in role_files(d, role_re):
        sections = list(section_text(d.texts[f], role_re))
        if sections:
            parts.extend(title for hf, _, title in d.headings if hf == f and role_re.search(title))
        parts.extend(sections or [d.texts[f]])
    return "\n".join(parts)


def has_content(text: str):
    """排除空白、勾选、占位与只有表头的段落；不判断内容真假。"""
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if re.match(r"^\s*#{1,6}\s", line):
            continue
        if (line.strip().startswith("|") and index + 1 < len(lines)
                and re.fullmatch(r"[|\s:-]+", lines[index + 1])):
            continue  # 只有表头而没有查询/回答行，不算内容。
        clean = re.sub(r"^[\s#>*\-+\[\]xX✓✅]+", "", line).strip()
        if not clean or re.fullmatch(r"[|\s:—–-]+", clean):
            continue
        if clean in {"待填", "待补", "TODO", "TBD", "无", "通过", "已检查"}:
            continue
        if len(re.sub(r"\W", "", clean)) >= 3:
            return True
    return False


def check_parts(d: Dossier):
    """⑦ 五个必备件 + 立案卡（文件名或 1–4 级标题命中即算有）。"""
    names = " ".join(d.top_files)
    top_heads = [h for (f, _, h) in d.headings if "/" not in f]
    missing = []
    for part, keys in PART_KEYS.items():
        if any(k in names for k in keys):
            continue
        if any(any(k in h for k in keys) for h in top_heads):
            continue
        missing.append(part)
    if missing:
        d.fail("必备件", "缺 " + "、".join(missing) + "（SOP ⑦）")


def main_files(d: Dossier):
    return [f for f in d.all_files if not SUPPLEMENT_RE.search(f)]


def check_query_log(d: Dossier):
    """闸 3：主检索的查询词须登记（补查件里的登记只覆盖补查本身）。

    只验「有没有登记痕迹」，不验登记全不全：查询类标题下须有实际内容；
    无此类标题时，查询标签行及其后 5 行、查询列中的带引号查询串合计 ≥3 即算有。
    """
    files = main_files(d)
    empty_heading = False
    for f in files:
        for body in section_text(d.texts[f], QUERY_HEADING_RE):
            if has_content(body):
                return
            empty_heading = True
    quoted = 0
    for f in files:
        lines = d.texts[f].split("\n")
        for k, line in enumerate(lines):
            if QUERY_LABEL_RE.search(line) and not line.lstrip().startswith("|"):
                quoted += sum(len(QUOTED_RE.findall(x)) for x in lines[k:k + 6])
            elif QUERY_LINE_RE.search(line) and not line.lstrip().startswith("|"):
                quoted += len(QUOTED_RE.findall(line))
    for t in d.tables:
        if t.file not in files:
            continue
        cols = [k for k, c in enumerate(t.header) if "查询" in c and "数" not in c]
        for r in t.rows:
            for k in cols:
                if k < len(r):
                    quoted += len(QUOTED_RE.findall(r[k]))
    if quoted >= 3:
        return
    if empty_heading:
        shape_issue(d, "查询登记", "查询标题下没有实际查询内容（闸 3）")
    else:
        d.fail("查询登记", "主检索未见查询词登记（闸 3）")


def ledger_tables(d: Dossier):
    out = []
    for t in d.tables:
        if SUPPLEMENT_RE.search(t.file):
            continue
        ctx = ("台账" in t.file) or ("台账" in t.heading) or ("证据" in t.heading)
        if not ctx:
            continue
        hdr = " | ".join(t.header)
        if URL_COL_RE.search(hdr) and any(GRADE_COL_RE.search(c) for c in t.header):
            out.append(t)
    return out


def check_ledger(d: Dossier):
    """⑦ 证据台账须有 URL 与档位列；每行要有 URL、日期、档位。v0.6 起 URL 不得截断。"""
    has_ledger_part = any("台账" in f or "证据" in f for f in d.top_files) or any(
        ("台账" in h or "证据" in h) for (f, _, h) in d.headings if "/" not in f
    )
    if not has_ledger_part:
        return  # 缺件已由必备件检查报
    tabs = ledger_tables(d)
    if not tabs:
        d.fail("台账列", "证据台账没有带 URL 列与档位列的表（SOP ⑦ 台账 schema）")
        return
    no_url = no_date = no_grade = trunc = 0
    total = valid = 0
    for t in tabs:
        ui = next((k for k, c in enumerate(t.header) if URL_COL_RE.search(c)), None)
        gi = next((k for k, c in enumerate(t.header) if GRADE_COL_RE.search(c)), None)
        for row_index, r in enumerate(t.rows, 1):
            if not any(c.strip() for c in r):
                continue
            total += 1
            row = " | ".join(r)
            if (ui is not None and ui < len(r) and not EMPTY_CELL_RE.match(r[ui])
                    and gi is not None and gi < len(r) and not EMPTY_CELL_RE.match(r[gi])
                    and DATE_RE.search(row)):
                valid += 1
            for date in re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", row):
                try:
                    datetime.date.fromisoformat(date)
                except ValueError:
                    shape_issue(d, "台账日期", f"{t.file}（{t.heading}）表内第 {row_index} 行 [{r[0] if r else '?'}] 日期非法：{date}")
            if ui is not None and (ui >= len(r) or EMPTY_CELL_RE.match(r[ui])):
                no_url += 1
            if ui is not None and ui < len(r) and "…" in r[ui]:
                trunc += 1
            if not DATE_RE.search(row):
                no_date += 1
            if gi is not None and (gi >= len(r) or EMPTY_CELL_RE.match(r[gi])):
                no_grade += 1
    if not valid:
        shape_issue(d, "台账有效行", "证据台账没有同时具备 URL/对象、日期与读深/档位的有效行")
    parts = []
    if no_url:
        parts.append(f"{no_url} 行无 URL")
    if no_date:
        parts.append(f"{no_date} 行无日期")
    if no_grade:
        parts.append(f"{no_grade} 行无档位")
    if parts:
        d.warn("台账行", f"台账 {total} 行中：" + "，".join(parts))
    if trunc and at_least(d, (0, 6)):
        d.warn("URL完整", f"{trunc} 行 URL 含「…」截断（v0.6 起要求完整）")


def check_ledger_axes(d: Dossier):
    """v1.3（建议 2）：台账须有读深、来源类型、信息状态三列（读深不再用 A/B/C 字母）。"""
    if not at_least(d, (1, 3)):
        return
    tabs = ledger_tables(d)
    if not tabs:
        return  # 无台账表已由台账列检查报
    need = ["读深", "来源类型", "信息状态"]
    best = max(tabs, key=lambda t: sum(any(n in c for c in t.header) for n in need))
    miss = [n for n in need if not any(n in c for c in best.header)]
    if miss:
        d.fail("台账三轴", "台账缺 v1.3 必备列：" + "、".join(miss))


def check_utc(d: Dossier):
    """闸 4（v1.2 起）：台账日期统一 UTC 记录。"""
    if not at_least(d, (1, 2)):
        return
    for f in d.all_files:
        if UTC_RE.search(d.texts[f]):
            return
    d.fail("UTC", "全 dossier 未见一处 UTC 时间戳（v1.2 闸 4）")


def coverage_tables(d: Dossier):
    out = []
    for t in d.tables:
        if SUPPLEMENT_RE.search(t.file):
            continue
        if not any(STATUS_HEADER_RE.search(c) for c in t.header):
            continue
        if not (COVERAGE_CTX_RE.search(t.file) or COVERAGE_CTX_RE.search(t.heading)):
            continue
        if INITIAL_CTX_RE.search(t.heading):
            continue
        out.append(t)
    return out


def status_index(t: Table):
    return next(k for k, c in enumerate(t.header) if STATUS_HEADER_RE.search(c))


def all_unopened(t: Table):
    si = status_index(t)
    vals = [r[si] for r in t.rows if si < len(r) and r[si].strip()]
    return bool(vals) and all(re.search(r"未开", v) for v in vals)


def check_gate5(d: Dossier):
    """闸 5：覆盖表还有未处置格子时不得出裁定 / 交账。"""
    products = role_files(d, PRODUCT_RE)
    if not products:
        return
    tabs = coverage_tables(d)
    # 立案卡里的覆盖矩阵若全是「未开」，是立案时的初始值：别处有终态表就忽略它，没有就报未回填
    initial = [t for t in tabs if "立案" in t.file and all_unopened(t)]
    final = [t for t in tabs if t not in initial]
    if initial and not final:
        d.fail("闸5", "覆盖表停在立案初始值（全部未开），没有回填终态，却已出产物（" + "、".join(products) + "）")
        return
    open_bad, open_ok = [], []
    for t in final:
        si = status_index(t)
        for r in t.rows:
            if si >= len(r):
                continue
            v = r[si]
            # 否定终态先于「完成」等子串，避免「未关闭，尚未完成」被放行。
            negated = re.search(r"未关|未完成|尚未完成|没有完成", v)
            if negated:
                if JUSTIFY_RE.search(" | ".join(r)):
                    open_ok.append(f"{r[0] if r else '?'}〔{v}〕")
                else:
                    shape_issue(d, "闸5", f"覆盖格子仍未处置：{r[0] if r else '?'}〔{v}〕")
                continue
            if CLOSED_RE.search(v) or not OPEN_RE.search(v):
                continue
            label = r[0] if r[0] else (r[1] if len(r) > 1 else "?")
            row = " | ".join(r)
            (open_ok if JUSTIFY_RE.search(row) else open_bad).append(f"{label}〔{v}〕")
    if open_bad:
        d.fail("闸5", "覆盖表有未处置格子却已出产物（" + "、".join(products) + "）：" + "；".join(open_bad))
    if open_ok:
        d.warn("闸5", "未关格子带了说明，需人看是否算处置：" + "；".join(open_ok))


def check_supplement_marks(d: Dossier):
    """v1.1 起：有补查件时，原裁定文件须加「补查修订」节或指向补查件。"""
    if not at_least(d, (1, 1)):
        return
    sups = [f for f in d.top_files if SUPPLEMENT_RE.search(f)]
    if not sups:
        return
    prods = [f for f in role_files(d, PRODUCT_RE) if not SUPPLEMENT_RE.search(f)]
    for p in prods:
        text = d.texts[p]
        if "补查修订" in text:
            continue
        if any(Path(s).stem.split("-")[0] + "-补查" in text or Path(s).stem in text for s in sups):
            continue
        d.fail("补查标记", f"{p} 没有补查修订节，也没指向 " + "、".join(sups) + "（v1.1 补查三件套②）")


def substantive_selfcheck_answer(text: str):
    """识别回答形状：排除题干、占位与勾选，接受有陈述谓词的直接回答。

    这是格式检查，不判断回答是否正确或覆盖了题意；长度本身不构成作答。
    """
    clean = re.sub(r"[*`]|^\s*(?:[-+>]\s*)?(?:\[[ xX]\]\s*)?", "", text).strip()
    if not has_content(clean):
        return False
    # 题干即使带有「已核对」「一致」等词，仍不能冒充肯定回答。
    if re.search(r"[？?]|是否|能否|有无|有没有|如何|怎么|哪些|什么|何处|哪里|吗(?:[。！!\s]|$)", clean):
        return False
    if re.fullmatch(r"(?:口径对表|同物同值|补查传到)[。！!\s]*", clean):
        return False
    if re.match(r"(?:请|务必|需要|须要|应当|应该|检查|核对|确认|比较|说明|回答|列出|对照|把|将)", clean):
        return False  # 无问号的祈使题干也不是作答。
    return bool(re.search(
        r"已|未|没有|不存在|一致|相同|不同|相符|均|不变|符合|满足|来自|源于|"
        r"记录|核对|落实|完成|确认|保持|采用|使用|基于|仅|兑现|完整|同值|无需|无须|尚无|^无", clean))


def check_selfcheck(d: Dossier):
    """v1.3（建议 3）：自查件必备，且须逐条作答交账前三问。"""
    if not at_least(d, (1, 3)):
        return
    has_part = any("自查" in f for f in d.top_files) or any("自查" in h for (f, _, h) in d.headings if "/" not in f)
    if not has_part:
        d.fail("自查件", "缺自查件（v1.3 ⑦ 第 8 件）")
    if not any("交账前三问" in t for t in d.texts.values()):
        d.fail("交账三问", "未见「交账前三问」作答（v1.3）")
        return
    # 仅验三项各有文字回答，支持编号列表、圈号小节和单文件章节。
    answered = set()
    item_re = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:([123])[.、．)）]|([①②③]))\s*(.*)$")
    for text in d.texts.values():
        for body in section_text(text, re.compile("交账前三问")):
            items = []
            for line in body.split("\n"):
                hit = item_re.match(line)
                if hit:
                    number = int(hit.group(1)) if hit.group(1) else "①②③".index(hit.group(2)) + 1
                    items.append([number, hit.group(3), []])
                elif items:
                    items[-1][2].append(line)
            for number, label, rest in items:
                # 有分隔符时取回答；没有时仍检查完整编号陈述，不丢弃整行。
                answer = re.split(r"[：:？?]", label, maxsplit=1)
                inline = answer[1] if len(answer) > 1 else label
                # 各回答行单独识别，避免题干小标题污染下面独立的实质回答。
                if any(substantive_selfcheck_answer(line) for line in [inline] + rest):
                    answered.add(number)
    if answered != {1, 2, 3}:
        shape_issue(d, "交账三问", "三问缺实质分项作答：" + "、".join(str(n) for n in sorted({1, 2, 3} - answered)))


def check_case_card(d: Dossier):
    """v1.3（建议 4）：立案卡须有「读法」与「关键假设」两项。"""
    if not at_least(d, (1, 3)):
        return
    text = role_text(d, re.compile("立案")) or "\n".join(d.texts[f] for f in d.top_files[:1])
    miss = [k for k in ("读法", "关键假设") if k not in text]
    if miss:
        d.fail("立案两项", "立案卡缺 " + "、".join(miss) + "（v1.3 ①）")


ENUM_RE = re.compile(r"枚举|盘点|选型|对比|找全|有哪些|清单|排名|前十|前 ?\d+|top ?\d+", re.I)


def check_recall_probe(d: Dossier):
    """v1.3（建议 5）：枚举 / 盘点 / 选型类议题须做召回探针并报相对召回率。"""
    if not at_least(d, (1, 3)):
        return
    card = role_text(d, re.compile("立案"))
    if not ENUM_RE.search(card):
        return
    alltext = "\n".join(d.texts.values())
    # v1.6 允许披露未预登记，不要求事后编造召回率；仍提示覆盖未证明。
    if at_least(d, (1, 6)):
        for line in alltext.splitlines():
            if re.fullmatch(r"召回探针：未预登记；原因：.+；相对召回：不可计算[。]?", line.strip()):
                d.warn("召回探针", "已披露未预登记；相对召回与覆盖完整性未验证")
                return
    if "召回探针" not in alltext:
        d.fail("召回探针", "枚举 / 盘点 / 选型类议题未做召回探针（v1.3 ⑥）")
    elif "相对召回" not in alltext:
        d.fail("召回探针", "做了召回探针但没报相对召回率（v1.3 ⑥）")


PRICE_RE = re.compile(r"价格|现价|报价|单价|价位|售价")


def check_registry(d: Dossier):
    """价格议题须登记；v1.6的P-004也涵盖一般数据，不再单凭预设触发。

    v1.3–v1.5仍保留P-004触发规则，价格识别及立案范围保持兼容。
    """
    if not at_least(d, (1, 3)) or NO_REGISTRY:
        return
    card = role_text(d, re.compile("立案"))
    legacy_preset = not at_least(d, (1, 6)) and "P-004" in card
    if not (PRICE_RE.search(card) or legacy_preset):
        return
    try:
        reg = VOLATILE_REGISTRY.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        d.fail("易变登记", "指定易变事实登记簿不存在、不可读或编码无效")
        return
    if d.name not in reg:
        d.fail("易变登记", "价格类 dossier 未在易变事实登记簿登记（v1.3 ⑦）")


CONTRA_TOK = re.compile(r"\b(?:[A-Z]{2,}[A-Za-z]*\d*|[A-Za-z]+\d+[A-Za-z0-9]*)\b")
CONTRA_FILE = re.compile(r"裁定|结论|交账|排序|汇总|主表|判断|杠杆|候选清单|对比矩阵|对标|台账|证据")


def check_contradiction(d: Dossier):
    """交账前三问第②问的粗查：同一术语在台账/裁定类文件里一处写「支持」、一处写「不支持」。

    只是提示（同一行讲多个对象时会误报），WARN 级，须人看。
    """
    pos, neg = {}, {}
    for f in d.top_files:
        if f not in role_files(d, CONTRA_FILE):
            continue
        for i, line in enumerate(d.texts[f].split("\n"), 1):
            if "不支持" in line:
                for t in set(CONTRA_TOK.findall(line)):
                    neg.setdefault(t, f"{f}:{i}")
            elif re.search(r"(?<!不)支持", line):
                for t in set(CONTRA_TOK.findall(line)):
                    pos.setdefault(t, f"{f}:{i}")
    both = sorted(set(pos) & set(neg))
    if both:
        shown = "；".join(f"{t}（{pos[t]} 写支持 / {neg[t]} 写不支持）" for t in both[:3])
        d.warn("对冲粗查", f"可能自相矛盾，须人看（粗查，常有误报）：{shown}")


def check_version_decl(d: Dossier):
    """v1.3（建议 9）：立案卡开头写「烛龙预设 P-00x · 日期 · 协议 v1.x」，兼作版本声明。"""
    if not at_least(d, (1, 3)):
        return
    if not d.version_declared:
        d.fail("版本声明", "立案卡未声明协议版本（按现行版检查；v1.3 ⑦「协议版本声明与预设行」）")
    if not any("烛龙预设" in d.texts[f] for f in d.top_files):
        d.fail("预设行", "未写「烛龙预设 P-00x · 日期 · 协议 v1.x」行（v1.3 ⑦）")


LIKELY_RE = re.compile(r"几乎不可能|很不可能|较不可能|五五开|较可能|很可能|几乎肯定")
CONF_RE = re.compile(r"[高中低]置信|置信度|置信[：:]")
PCT_RE = re.compile(r"\d{1,2}\s?[–\-~～]\s?\d{1,3}\s?%")


def check_expression(d: Dossier):
    """v1.3（建议 10）：置信度与可能性词不同句；用了可能性词须至少一处括注百分比区间。"""
    # v1.6 改为按证据状态表达，未经校准不强迫配数字概率；旧档案仍按旧条款检查。
    if not at_least(d, (1, 3)) or at_least(d, (1, 6)):
        return
    mixed, used, pct = [], False, False
    for f, _, line, _ in relation_lines(d):
        if line.lstrip().startswith("|"):
            continue
        for sent in re.split(r"[。！？；\n]", line):
            has_l, has_c = LIKELY_RE.search(sent), CONF_RE.search(sent)
            used = used or bool(has_l)
            pct = pct or bool(PCT_RE.search(sent))
            if has_l and has_c:
                mixed.append(f"{f}：{sent.strip()[:40]}")
    if mixed:
        d.fail("结论表达", "置信度与可能性写在同一句（v1.3 结论表达）：" + "；".join(mixed[:3]))
    if used and not pct:
        d.warn("结论表达", "用了可能性词却没有一处括注百分比区间（v1.3 结论表达）")


def check_candidate_submit(d: Dossier):
    """v1.3（建议 8）：补查修订件第③条改为「提交候补条款」，补查件里须写明提交到候补队列。"""
    if not at_least(d, (1, 3)):
        return
    sups = [f for f in d.top_files if SUPPLEMENT_RE.search(f)]
    for s in sups:
        if "候补" not in d.texts[s] and "candidate-clauses" not in d.texts[s]:
            d.warn("候补提交", f"{s} 未写明教训已提交候补条款队列（v1.3 补查三件套③）")


def check_volatile(d: Dossier):
    """证据不得只存在本机易失路径（Temp 等）。"""
    hits = [f for f in d.all_files if VOLATILE_RE.search(d.texts[f])]
    if hits:
        d.warn("证据留存", "引用了本机易失路径（Temp 等）：" + "、".join(hits))


# ---------- 关系层（v1.5）：比较 / 派生句的操作数是否同口径、是否触了禁用标记 ----------
NOBEAR_MARK = "〔不得承重〕"
EXEMPT_RE = re.compile(r"〔(?:继承 ?S-\d{3} ?例外|承重例外)[：:][^〕]+〕")
META_MARK = "〔口径说明〕"
REL_KW = re.compile(r"差|贵|便宜|倍|×|%|％|档|每元|每瓦|性价比|领先|反超|高于|低于|划算|替代|压过|占比|比值")
AMOUNT_RE = re.compile(r"[¥￥$]\s?(\d[\d,]*(?:\.\d+)?)(?:\s?[–\-~～]\s?[¥￥$]?\s?(\d[\d,]*(?:\.\d+)?))?")
EID_RE = re.compile(r"\b([ENVR]\d{2,3})\b")
LAUNCH_PRICE_RE = re.compile(r"SEP|MSRP|发布价|首发价|建议零售价|官方定价")
SCOPED_RE = re.compile(r"SEP|MSRP|发布价|首发价|建议零售价|按 ?\d{4}-\d{2}")
DECISION_HEAD_RE = re.compile(r"选型|建议|推荐|怎么买|该买")
PEAK_RE = re.compile(r"TFLOPS|TFLOPs|TOPS|TOPs|FP32|FP16|FP8|INT8|INT4|算力")
PEAK_REL_RE = re.compile(r"倍|×|%|％|领先|压过|反超|高于|低于|每元|每瓦")
VENDOR_GROUPS = {
    "AMD": re.compile(r"\bAMD\b|Radeon|RDNA|CDNA|Instinct"),
    "NVIDIA": re.compile(r"NVIDIA|GeForce|\bRTX\b|Blackwell|\bAda\b|Hopper"),
    "Intel": re.compile(r"\bIntel\b|\bArc [AB]\d"),
    "Apple": re.compile(r"\bApple\b|\bM[1-5] (?:Pro|Max|Ultra)\b"),
}
STALE_DAYS = 30
_REGISTRY_CACHE = None
_REGISTRY_ERROR = None
NO_REGISTRY = False


@dataclass
class LedgerRow:
    rid: str
    text: str
    dates: list
    amounts: set
    reasons: list
    marked: bool
    exempt: bool
    file: str = "<未知文件>"  # 兼容旧的七参数构造调用

    @property
    def nobear(self):
        return self.marked or (bool(self.reasons) and not self.exempt)


def norm_amounts(text: str) -> set:
    out = set()
    for m in AMOUNT_RE.finditer(text):
        for g in m.groups():
            if g:
                v = g.replace(",", "")
                try:
                    if float(v) >= 100:
                        out.add(v.rstrip("0").rstrip(".") if "." in v else v)
                except ValueError:
                    pass
    return out


def load_registry_rules():
    """加载显式登记规则；空文件表示有意不设规则，损坏或缺失须报告。"""
    global _REGISTRY_CACHE, _REGISTRY_ERROR
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    s_rules, v_status = [], {}
    try:
        if NO_REGISTRY:
            text = ""
        else:
            text = VOLATILE_REGISTRY.read_text(encoding="utf-8")
        # 代码块只是示例；有效正文中的规则行则必须被合法表格消费。
        body, fence = [], None
        for line in text.splitlines():
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if fence:
                if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                    fence = None
                body.append("")
            elif marker:
                fence = (marker[1][0], len(marker[1]))
                body.append("")
            else:
                body.append("" if line.startswith(("    ", "\t")) else line)
        registry_text = "\n".join(body)
        declared_ids = re.findall(r"^ {0,3}\|?\s*([SV]-[^\s|]+)\s*\|", registry_text, re.M)
        recognized = False
        seen = set()
        for t in parse_tables("registry", registry_text):
            hdr = t.header
            ids = [row[0].strip() for row in t.rows if row]
            source_table = any(rid.startswith('S-') for rid in ids) or any('识别词' in col for col in hdr)
            fact_table = any(rid.startswith('V-') for rid in ids)
            if source_table and (not hdr or hdr[0].strip() != '#' or hdr.count('识别词') != 1 or fact_table):
                raise ValueError('来源规则表头损坏或混入事实记录')
            if fact_table and (not hdr or hdr[0].strip() != '#' or hdr.count('状态') != 1):
                raise ValueError('事实记录表头损坏')
            if not hdr or hdr[0].strip() != "#":
                continue
            if "识别词" in hdr:
                recognized = True
                ki = hdr.index("识别词")
                for row in t.rows:
                    if not row or not re.fullmatch(r"S-\d{3}", row[0].strip()) or ki >= len(row):
                        raise ValueError("来源规则行编号或字段不完整")
                    rid = row[0].strip()
                    words = [w.strip() for w in re.split(r"[；;]", row[ki].replace("`", "")) if w.strip()]
                    if not words or rid in seen:
                        raise ValueError("来源规则缺识别词或编号重复")
                    seen.add(rid)
                    s_rules.append((rid, words))
            elif "状态" in hdr:
                recognized = True
                si = hdr.index("状态")
                for row in t.rows:
                    if not row or not re.fullmatch(r"V-\d{3}", row[0].strip()) or si >= len(row):
                        raise ValueError("事实登记行编号或字段不完整")
                    rid = row[0].strip()
                    if not row[si].strip() or rid in seen:
                        raise ValueError("事实登记状态为空或编号重复")
                    seen.add(rid)
                    v_status[rid] = row[si]
        if len(declared_ids) != len(seen) or set(declared_ids) != seen:
            raise ValueError("登记规则行未被合法表格完整解析")
        if text.strip() and not recognized:
            raise ValueError("登记簿没有受支持的来源规则表或事实记录表")
    except (OSError, UnicodeError, ValueError) as exc:
        _REGISTRY_ERROR = f"{type(exc).__name__}：登记簿缺失、不可读或格式无效"
        s_rules, v_status = [], {}
    _REGISTRY_CACHE = (s_rules, v_status)
    return _REGISTRY_CACHE


def ledger_rows(d: Dossier):
    s_rules, v_status = load_registry_rules()
    rows = []
    for t in ledger_tables(d):
        ti = next((k for k, c in enumerate(t.header) if "来源类型" in c), None)
        ii = next((k for k, c in enumerate(t.header) if "信息状态" in c), None)
        di = next((k for k, c in enumerate(t.header) if "读深" in c), None)
        for r in t.rows:
            if not r or not r[0].strip():
                continue
            text = " | ".join(r)
            reasons = []
            for sid, words in s_rules:
                hit = next((w for w in words if w and w in text), None)
                if hit:
                    reasons.append(f"命中 {sid} 识别词「{hit}」")
            for vid in set(re.findall(r"V-\d{3}", text)):
                st = v_status.get(vid, "")
                if re.search(r"过时|被推翻", st):
                    reasons.append(f"引用 {vid}（{re.sub(r'[*]', '', st)[:12]}）")
            if ti is not None and ii is not None and ti < len(r) and ii < len(r):
                if (re.search(r"聚合|用户口径", r[ti])
                        and not re.fullmatch(r"独立双源证实", r[ii].strip().strip("*"))):
                    reasons.append("聚合或用户口径且非独立双源证实")
            if di is not None and di < len(r) and re.search(r"摘要", r[di]):
                reasons.append("只到摘要级")
            dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})", text)
            rows.append(LedgerRow(r[0].strip().strip("*` "), text, dates, norm_amounts(text), reasons,
                                  NOBEAR_MARK in text, bool(EXEMPT_RE.search(text)), t.file))
    return rows


def relation_lines(d: Dossier):
    """产物类与补查类文件里的行（去掉删除线片段），带所在标题路径；跳过作废与口径说明行。"""
    files = sorted(set(role_files(d, PRODUCT_RE) + role_files(d, SUPPLEMENT_RE)))
    for f in files:
        stack = []
        for i, line in enumerate(d.texts[f].split("\n"), 1):
            m = re.match(r"^(#{1,6})\s+(.*)$", line)
            if m:
                lvl = len(m.group(1))
                stack = [s for s in stack if s[0] < lvl] + [(lvl, m.group(2))]
                continue
            live = re.sub(r"~~.*?~~", "", line)
            if "作废" in live or META_MARK in live or not live.strip():
                continue
            head = " / ".join(s[1] for s in stack)
            # 角色按标题祖先继承；混合文档的立案/台账/自查不能充作结论。
            excluded = re.compile(r"立案|覆盖|证据台账|证据清单|证据条目|(?:^|[、. ])证据$|台账|未知桶|碰壁|待开|自查|查询")
            if any(excluded.search(title) for _, title in stack):
                continue
            if not (PRODUCT_RE.search(f) or SUPPLEMENT_RE.search(f)
                    or any(PRODUCT_RE.search(title) or SUPPLEMENT_RE.search(title) for _, title in stack)):
                continue
            yield f, i, live, head


def rel_level(d: Dossier):
    return "FAIL" if at_least(d, (1, 5)) else "WARN"


def check_relation(d: Dossier):
    """v1.5 关系层四项（v1.5 前的 dossier 只报 WARN）：

    ① 过时来源未标：台账行命中登记簿 S 行识别词 / 引用过时 V 行 / 聚合或用户口径非独立双源证实 / 只到摘要级，却没标〔不得承重〕或例外；
    ② 不得承重进关系句：比较 / 派生 / 档位句引用了不得承重的台账行（按编号或按数值）；
    ③ 旧价做现行比较：比较句里的价格只来自发布价或决策窗外的台账行，且没写明「按发布价」，或写进了选型建议；
    ④ 跨厂峰值未写计数规则：跨厂商比较纸面峰值（TFLOPS / TOPS 等）却全文没有「计数规则」。
    """
    lvl = rel_level(d)
    add = d.fail if lvl == "FAIL" else d.warn
    rows = ledger_rows(d)
    # 仅提示编号承重状态冲突，不推断引用目标，也不改变现有禁用判定。
    # 此词法子集不覆盖纯数字、点号及中文编号；产品代号仍可能误报。
    by_id = {}
    for row in rows:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*[0-9][A-Za-z0-9_-]*", row.rid):
            by_id.setdefault(row.rid, []).append(row)
    for rid, entries in sorted(by_id.items()):
        if {row.nobear for row in entries} == {False, True}:
            states = sorted({(row.file, row.nobear) for row in entries})
            detail = "；".join(
                f"{file}（{'不得承重' if blocked else '未被现有规则判为不得承重'}）"
                for file, blocked in states
            )
            d.warn("证据编号承重状态冲突", f"{rid}：{detail}；引用归属不明，需人工核对")
    # ①
    bad = [r for r in rows if r.reasons and not r.marked and not r.exempt]
    if bad:
        shown = "；".join(f"{r.rid}（{r.reasons[0]}）" for r in bad[:5])
        more = f" 等 {len(bad)} 行" if len(bad) > 5 else ""
        add("过时来源未标", f"台账行触到禁用来源却没标〔不得承重〕：{shown}{more}（v1.5 ⑤ 承重标记）")
    nob = [r for r in rows if r.nobear]
    nob_ids = {r.rid for r in nob if r.rid}
    # 由实际台账ID建立字面引用模式，兼容E01及D01-S1，且不把S1误配成S10。
    id_pattern = re.compile(
        r"(?<![A-Za-z0-9_-])(?:" + "|".join(
            re.escape(rid) for rid in sorted(nob_ids, key=lambda value: (-len(value), value))
        ) + r")(?![A-Za-z0-9_-])"
    ) if nob_ids else None
    nob_amts = set().union(*[r.amounts for r in nob]) if nob else set()
    ok_amts = set().union(*[r.amounts for r in rows if not r.nobear]) if rows else set()
    only_nob = nob_amts - ok_amts  # 只出现在不得承重行里的数值；两边都有的数值有歧义，只按编号判
    hits2, hits3 = [], []
    try:
        ddate = datetime.date.fromisoformat(d.date)
    except ValueError:
        ddate = None

    def stale(r):
        if LAUNCH_PRICE_RE.search(r.text):
            return True
        ds = []
        for value in r.dates:
            try:
                ds.append(datetime.date.fromisoformat(value))
            except ValueError:
                pass  # check_ledger 已逐行报告非法日期，继续检查其余 dossier。
        return bool(ds) and (ddate - min(ds)).days > STALE_DAYS

    for f, i, live, head in relation_lines(d):
        if not REL_KW.search(live):
            continue
        amts = norm_amounts(live)
        cited = set(id_pattern.findall(live)) if id_pattern else set()
        if cited or (amts & only_nob):
            hits2.append(f"{f}:{i}")
        if amts and ddate is not None:
            src = [r for r in rows if r.amounts & amts and not r.nobear]
            # 价格只有「发布价或决策窗外」的可用行支撑、没有决策窗内的可用行 ⇒ 旧价做了现行比较
            if src and all(stale(r) for r in src):
                if not SCOPED_RE.search(live) or DECISION_HEAD_RE.search(head):
                    hits3.append(f"{f}:{i}")
    if hits2:
        add("不得承重进关系句", "比较 / 派生 / 档位句用了不得承重的值：" + "、".join(hits2[:6]) + "（v1.5 ⑤）")
    if hits3:
        add("旧价做现行比较", "比较句的价格只来自发布价或决策窗外的台账行，且未限定为发布价或进了选型建议："
            + "、".join(hits3[:6]) + "（v1.5 ⑤ 时点用途）")
    # ④ 只看立案卡与产物标题判断「是否跨厂比较」；须有以「计数规则」为题的小节——正文或修订标记里出现这四个字不算
    scope = role_text(d, re.compile("立案"))
    products = set(role_files(d, PRODUCT_RE))
    scope += "\n" + "\n".join(h for (f, _, h) in d.headings if f in products)
    groups = [g for g, rx in VENDOR_GROUPS.items() if rx.search(scope)]
    has_rule = any("计数规则" in h for (_, _, h) in d.headings)
    if len(groups) >= 2 and not has_rule:
        ex = [f"{f}:{i}" for f, i, live, head in relation_lines(d)
              if PEAK_RE.search(live) and PEAK_REL_RE.search(live)]
        if ex:
            add("跨厂计数规则", f"跨厂商（{'/'.join(groups)}）比较纸面峰值却没写「计数规则」："
                + "、".join(ex[:4]) + "（v1.5 P-005 跨架构纸面指标）")


CHECKS = [
    check_parts, check_query_log, check_ledger, check_ledger_axes, check_utc, check_gate5,
    check_supplement_marks, check_selfcheck, check_case_card, check_recall_probe, check_registry,
    check_version_decl, check_expression, check_candidate_submit, check_contradiction, check_volatile,
    check_relation,
]


def run(paths):
    global _REGISTRY_CACHE, _REGISTRY_ERROR
    _REGISTRY_CACHE, _REGISTRY_ERROR = None, None
    load_registry_rules()
    out = []
    for p in paths:
        d = load(p)
        if _REGISTRY_ERROR:
            d.fail("登记簿读取", _REGISTRY_ERROR)
        if NO_REGISTRY:
            d.warn("登记簿未检查", "显式跳过登记簿；来源失效规则与价格登记未经检查")
        for c in CHECKS:
            c(d)
        out.append(d)
    return out


# ---------- 随包合成正反对照 ----------
SELF_CHECK = [("invalid", "必备件", "FAIL")]
SELF_CHECK_PASS = ["minimal"]


def self_check(results):
    by = {d.name: d for d in results}
    ok = True
    executed = 0
    lines = []
    for name, check, level in SELF_CHECK:
        d = by.get(name)
        if d is None:
            lines.append(f"SKIP  {name} · {check}（目录不在本机）")
            continue
        executed += 1
        hit = any(f.check == check and f.level == level for f in d.findings)
        ok &= hit
        lines.append(f"{'PASS' if hit else 'MISS'}  {name} · 应抓到 {check} {level}")
    for name in SELF_CHECK_PASS:
        d = by.get(name)
        if d is None:
            lines.append(f"SKIP  {name} · 应通过（目录不在本机）")
            continue
        executed += 1
        clean = d.status == "PASS"
        ok &= clean
        lines.append(f"{'PASS' if clean else 'MISS'}  {name} · 应无任何 FAIL/WARN（实为 {d.status}）")
    if not executed:
        lines.append("MISS  无实际执行的阳性/阴性对照，不能判自测通过")
    return ok and executed > 0, lines


def spot_check_due():
    """常设抽查（建议 6）：读台账里的抽样池基线，数基线之后新增的 dossier。

    按目录名排序比较（日期前缀）；同一天里名字排在基线前面的新 dossier 会漏数，属已知局限。
    """
    try:
        text = SPOT_LEDGER.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"抽样池基线:\s*(\S+)\s*-->", text)
    if not m:
        return None
    base = m.group(1)
    newer = sorted(p.name for p in DOSSIER_ROOT.iterdir() if p.is_dir() and p.name > base)
    return base, newer


def main():
    global VOLATILE_REGISTRY, NO_REGISTRY
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="烛龙 dossier 体检（只读）")
    ap.add_argument("dossiers", nargs="*", help="dossier 目录；缺省 = docs/zhulong/dossiers/ 下全部")
    ap.add_argument("--json", action="store_true", help="机读输出")
    ap.add_argument("--strict", action="store_true", help="有 FAIL 即退出码 1")
    ap.add_argument("--self-check", action="store_true", help="随包合成正反对照；缺省检查 examples")
    registry = ap.add_mutually_exclusive_group()
    registry.add_argument("--registry", type=Path, help="项目自己的易变事实登记簿")
    registry.add_argument("--no-registry", action="store_true", help="明确跳过登记簿并产生 WARN")
    ap.add_argument("--fail-on-warn", action="store_true", help="FAIL 或 WARN 均返回非零")
    a = ap.parse_args()
    if a.self_check and (a.json or a.strict or a.fail_on_warn or a.registry or a.no_registry):
        ap.error("--self-check 请单独运行；不与 JSON、严格/警告拦截或登记簿选项组合")
    if a.registry:
        VOLATILE_REGISTRY = a.registry.resolve()
    NO_REGISTRY = a.no_registry
    try:
        if a.dossiers:
            paths = [Path(p).resolve() for p in a.dossiers]
        elif a.self_check:
            paths = [REPO / "examples" / "minimal", REPO / "examples" / "invalid"]
        else:
            if not DOSSIER_ROOT.is_dir():
                ap.error("没有默认研究目录；请显式提供要检查的目录")
            paths = sorted(p for p in DOSSIER_ROOT.iterdir() if p.is_dir())
        if not paths:
            ap.error("没有研究记录可检查；未执行检查")
        if any(not p.is_dir() for p in paths):
            ap.error("指定路径不存在或不是目录")
    except OSError as exc:
        ap.error(f"无法列出研究目录：{type(exc).__name__}")
    results = run(paths)
    if a.json:
        print(json.dumps([
            {"dossier": d.name, "version": "v%d.%d" % d.version, "version_declared": d.version_declared,
             "status": d.status, "scope": "structural_heuristics_only",
             "registry": {"enabled": not NO_REGISTRY, "error": _REGISTRY_ERROR,
                          "source_rules": len(_REGISTRY_CACHE[0]), "fact_rules": len(_REGISTRY_CACHE[1])},
             "findings": [f.__dict__ for f in d.findings]} for d in results
        ], ensure_ascii=False, indent=2))
    else:
        for d in results:
            vs = "v%d.%d" % d.version + ("" if d.version_declared else "（按日期推断）")
            print(f"[{d.status}] {d.name}  协议 {vs}")
            for f in d.findings:
                print(f"    {f.level}  {f.check}：{f.msg}")
        n = {s: sum(1 for d in results if d.status == s) for s in ("PASS", "WARN", "FAIL")}
        print(f"\n合计 {len(results)} 份：PASS {n['PASS']} / WARN {n['WARN']} / FAIL {n['FAIL']}")
        print("注：PASS 只说明形状与已机械化的关系项过了，不说明结论对。")
        print(f"登记簿：{'未检查' if NO_REGISTRY else '已读取' if not _REGISTRY_ERROR else '读取失败'}；来源规则 {len(_REGISTRY_CACHE[0])} / 事实规则 {len(_REGISTRY_CACHE[1])}")
        if not a.dossiers and not a.self_check:
            due = spot_check_due()
            if due:
                base, newer = due
                tag = "，抽查到期" if len(newer) >= SPOT_EVERY else ""
                print(f"常设抽查：基线 {base} 之后新增 {len(newer)} 份（每 {SPOT_EVERY} 份抽 1 份{tag}）")
    if a.self_check:
        ok, lines = self_check(results)
        print("\n量具阳性对照：")
        for line in lines:
            print("  " + line)
        print("结论：" + ("通过" if ok else "未通过"))
        if not ok:
            return 1
    if a.fail_on_warn and any(d.status != "PASS" for d in results):
        return 1
    if a.strict and any(d.status == "FAIL" for d in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
