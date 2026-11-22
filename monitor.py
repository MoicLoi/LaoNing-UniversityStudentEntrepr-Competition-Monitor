#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
辽宁省大学生创新创业管理共享平台（https://cxcy.upln.cn）赛事监听器

监听对象（无需登录）：
  列表  GET https://cxcy.upln.cn/provincial/match/competition/queryOngoing?year=&code=&pageNo=&pageSize=
  详情  GET https://cxcy.upln.cn/provincial/match/competition/detail?id=<赛事ID>

可发现的变更：
  新增 / 下架赛事、赛事名称与起止时间、主办承办、正文（HTML 转纯文本）改版、
  附件增删替换、报名项目数 itemCount、参与人数 memberNum、
  标签页归属变化（未开始→进行中→已结束）、奖项公示状态、证书发布状态。

仅用标准库，Python 3.8+ 可直接运行。
"""

import argparse
import concurrent.futures as futures
import hashlib
import html as htmllib
import json
import os
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

BASE = "https://cxcy.upln.cn/provincial"
LIST_PATH = "/match/competition/queryOngoing"
DETAIL_PATH = "/match/competition/detail"

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
STATE_PATH = os.path.join(DATA, "state.json")
DETAIL_DIR = os.path.join(DATA, "details")
EVENTS_PATH = os.path.join(DATA, "events.jsonl")
CHANGES_PATH = os.path.join(DATA, "changes.md")
CONFIG_PATH = os.path.join(HERE, "config.json")
LOCK_PATH = os.path.join(DATA, ".lock")

DEFAULT_CONFIG = {
    "years": "auto",              # auto = 本年度(及可配置的邻近年度)；也可写 [2026, 2025]
    "year_offsets": [-1, 0, 1],   # years=auto 时监听的年度偏移
    "tabs": [1, 2, 3, 4],         # 1全部 2进行中 3未开始 4已结束
    "interval_minutes": 10,       # --loop 模式轮询间隔
    "detail_sweep_hours": 6,      # 全量抓取详情正文的间隔（列表/统计字段每轮都抓）
    "detail_workers": 4,
    "request_delay_sec": 0.35,    # 每次请求之间的礼貌间隔
    "timeout_sec": 30,
    "retries": 3,
    "notify_command": "",         # 可选，例如 "msg %USERNAME% {text}"，占位符 {title} {text}
    "max_detail_chars_in_state": 0  # 正文另存 data/details/*.txt，state 里只留摘要
}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://cxcy.upln.cn/match",
    "Origin": "https://cxcy.upln.cn",
    "Connection": "close",
}

FIELD_LABELS = {
    "name": "赛事名称",
    "startTime": "开始时间",
    "endTime": "结束时间",
    "host": "主办单位",
    "coOrganizer": "协办单位",
    "undertakeName": "承办单位",
    "synopsis": "简介",
    "finishFlag": "结束标记",
    "finishTime": "实际结束时间",
    "awardPublicStatus": "奖项公示状态",
    "awardPublicTime": "奖项公示开始",
    "awardPublicEndTime": "奖项公示结束",
    "certificatePublishStatus": "证书发布状态",
    "itemCount": "报名项目数",
    "memberNum": "参与人数",
    "files": "附件",
    "tabs": "所属标签页",
    "detail": "详情正文",
    "coverUrl": "封面",
}

TAB_NAMES = {1: "全部", 2: "进行中", 3: "未开始", 4: "已结束"}
STATUS_FLAG = {0: "否/未开放", 1: "是/已开放", None: "未知"}


# ----------------------------------------------------------------------------- 基础工具

def now_local():
    return datetime.now().astimezone()


def iso(ts=None):
    return (ts or now_local()).strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs():
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(DETAIL_DIR, exist_ok=True)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        loaded = load_json(CONFIG_PATH, {})
        if isinstance(loaded, dict):
            cfg.update(loaded)
    return cfg


SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE
# 站点使用较旧的 TLS 实现，需要允许 legacy renegotiation，否则握手报
# UNSAFE_LEGACY_RENEGOTIATION_DISABLED（OpenSSL 3 默认禁用）。
_LEGACY = getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
SSL_CTX.options |= _LEGACY
try:
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        SSL_CTX.minimum_version = ssl.TLSVersion.TLSv1
except Exception:  # noqa: BLE001
    pass


def http_get_json(url, cfg, tries=None):
    tries = tries or cfg["retries"]
    last = None
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=cfg["timeout_sec"], context=SSL_CTX) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = "HTTP %s %s" % (e.code, url)
            if e.code in (401, 403):
                raise RuntimeError("接口拒绝访问（%s），可能需要登录：%s" % (e.code, url))
        except Exception as e:  # noqa: BLE001 - 网络抖动统一重试
            last = "%s: %s" % (type(e).__name__, e)
        if attempt < tries:
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError("请求失败：%s（%s）" % (url, last))


# ----------------------------------------------------------------------------- 抓取

def resolve_years(cfg):
    spec = cfg.get("years")
    if isinstance(spec, list) and spec:
        return [str(y) for y in spec]
    this_year = now_local().year
    offsets = cfg.get("year_offsets") or [0]
    return [str(this_year + int(o)) for o in offsets]


def fetch_list(year, cfg):
    """抓取某年度所有标签页，按 id 去重，合并 tab 归属。"""
    items = {}
    for code in cfg["tabs"]:
        page = 1
        pages = 1
        while page <= pages:
            qs = urllib.parse.urlencode({
                "year": year, "code": code, "pageNo": page, "pageSize": 100,
            })
            url = "%s%s?%s" % (BASE, LIST_PATH, qs)
            payload = http_get_json(url, cfg)
            if not payload.get("success"):
                raise RuntimeError("列表接口返回失败：%s %s" % (url, payload.get("message")))
            result = payload.get("result") or {}
            pages = int(result.get("pages") or 1) or 1
            for rec in result.get("records") or []:
                rid = rec.get("id")
                if not rid:
                    continue
                entry = items.setdefault(rid, {
                    "id": rid,
                    "name": rec.get("name"),
                    "year": rec.get("year") or str(year),
                    "startTime": rec.get("startTime"),
                    "endTime": rec.get("endTime"),
                    "createTime": rec.get("createTime"),
                    "synopsis": rec.get("synopsis"),
                    "coverUrl": stable_url(rec.get("coverUrl")),
                    "coverUrlRaw": rec.get("coverUrl"),
                    "tabs": [],
                })
                if code not in entry["tabs"]:
                    entry["tabs"].append(code)
            page += 1
            if page > pages:
                break
            time.sleep(cfg["request_delay_sec"])
        time.sleep(cfg["request_delay_sec"])
    for entry in items.values():
        entry["tabs"] = sorted(entry["tabs"])
    return items


def fetch_detail(rid, cfg):
    qs = urllib.parse.urlencode({"id": rid})
    url = "%s%s?%s" % (BASE, DETAIL_PATH, qs)
    payload = http_get_json(url, cfg)
    if not payload.get("success"):
        raise RuntimeError("详情接口返回失败：%s %s" % (rid, payload.get("message")))
    return payload.get("result") or {}


def stable_url(u):
    """去掉 ?ticket=... 这类每次请求都会变的临时参数，只保留稳定路径。"""
    if not u or not isinstance(u, str):
        return u
    return u.split("?", 1)[0]


def html_to_text(src):
    if not src:
        return ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", src)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|table|section)>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", "", s)
    s = htmllib.unescape(s)
    s = s.replace("\u00a0", " ").replace("\u3000", " ")
    lines = []
    for line in s.splitlines():
        line = unicodedata.normalize("NFKC", line).strip()
        line = re.sub(r"[ \t]+", " ", line)
        if line:
            lines.append(line)
    return "\n".join(lines)


def pick_detail_fields(d):
    files = []
    for f in d.get("fileList") or []:
        files.append({
            "name": f.get("name"),
            "size": int(f.get("size") or 0),
            "url": f.get("url"),
            "type": f.get("type"),
        })
    files.sort(key=lambda x: (x.get("name") or "", x.get("size") or 0))
    text = html_to_text(d.get("detail"))
    return {
        "name": d.get("name"),
        "year": d.get("year"),
        "startTime": d.get("startTime"),
        "endTime": d.get("endTime"),
        "host": d.get("host"),
        "coOrganizer": d.get("coOrganizer"),
        "undertakeName": d.get("undertakeName"),
        "synopsis": d.get("synopsis"),
        "finishFlag": d.get("finishFlag"),
        "finishTime": d.get("finishTime"),
        "awardPublicStatus": d.get("awardPublicStatus"),
        "awardPublicTime": d.get("awardPublicTime"),
        "awardPublicEndTime": d.get("awardPublicEndTime"),
        "certificatePublishStatus": d.get("certificatePublishStatus"),
        "itemCount": d.get("itemCount"),
        "memberNum": d.get("memberNum"),
        "coverUrl": stable_url(d.get("coverUrl")),
        "coverUrlRaw": d.get("coverUrl"),
        "files": files,
        "detail_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else "",
        "detail_chars": len(text),
    }, text


# ----------------------------------------------------------------------------- 差异比较

def short(v, limit=120):
    if v is None:
        return "（空）"
    if isinstance(v, list):
        if v and isinstance(v[0], dict) and "name" in v[0]:
            return "、".join((x.get("name") or "?") for x in v)
        return "、".join(str(x) for x in v) if v else "（空）"
    s = str(v)
    return s if len(s) <= limit else s[:limit] + "…"


def diff_text_lines(old, new, ctx=2, max_hunks=6):
    import difflib
    a = (old or "").splitlines()
    b = (new or "").splitlines()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out = []
    shown = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        shown += 1
        if shown > max_hunks:
            out.append("…（更多差异略）")
            break
        if tag in ("delete", "replace"):
            for line in a[i1:i2][:ctx + 2]:
                out.append("- " + line)
        if tag in ("insert", "replace"):
            for line in b[j1:j2][:ctx + 2]:
                out.append("+ " + line)
    return out


def compare_item(old, new, old_text, new_text):
    """返回 (events, needs_review)。"""
    events = []
    for field in ("name", "startTime", "endTime", "host", "coOrganizer", "undertakeName",
                  "synopsis", "finishFlag", "finishTime", "awardPublicStatus",
                  "awardPublicTime", "awardPublicEndTime", "certificatePublishStatus",
                  "itemCount", "memberNum", "coverUrl"):
        if field not in old and field not in new:
            continue
        ov, nv = old.get(field), new.get(field)
        if ov != nv:
            kind = "stats" if field in ("itemCount", "memberNum") else "field"
            if field in ("awardPublicStatus", "certificatePublishStatus", "finishFlag"):
                kind = "status"
            events.append({
                "type": kind,
                "field": field,
                "label": FIELD_LABELS.get(field, field),
                "old": ov,
                "new": nv,
                "note": "%s：%s → %s" % (FIELD_LABELS.get(field, field), short(ov), short(nv)),
            })
    if old.get("tabs") != new.get("tabs"):
        events.append({
            "type": "status",
            "field": "tabs",
            "label": FIELD_LABELS["tabs"],
            "old": old.get("tabs"),
            "new": new.get("tabs"),
            "note": "标签页：%s → %s" % (
                "/".join(TAB_NAMES.get(t, str(t)) for t in (old.get("tabs") or [])) or "无",
                "/".join(TAB_NAMES.get(t, str(t)) for t in (new.get("tabs") or [])) or "无",
            ),
        })
    of, nf = old.get("files") or [], new.get("files") or []

    def sig(f):
        # 附件下载地址里的 ticket 每次请求都会变，必须排除，否则每轮都误报
        return (f.get("name"), int(f.get("size") or 0), f.get("type"))

    if sorted(sig(x) for x in of) != sorted(sig(x) for x in nf):
        on = {(x.get("name"), int(x.get("size") or 0)) for x in of}
        nn = {(x.get("name"), int(x.get("size") or 0)) for x in nf}
        added = [n for n, _ in nn - on]
        removed = [n for n, _ in on - nn]
        note = "附件变更"
        if added:
            note += "；新增：" + "、".join(added)
        if removed:
            note += "；移除：" + "、".join(removed)
        if not added and not removed:
            note += "；同名附件大小变化（可能被替换）"
        events.append({
            "type": "files", "field": "files", "label": FIELD_LABELS["files"],
            "old": [x.get("name") for x in of], "new": [x.get("name") for x in nf],
            "note": note,
        })
    if old.get("detail_hash") and new.get("detail_hash") and old.get("detail_hash") != new.get("detail_hash"):
        lines = diff_text_lines(old_text, new_text)
        events.append({
            "type": "detail", "field": "detail", "label": FIELD_LABELS["detail"],
            "old": old.get("detail_hash"), "new": new.get("detail_hash"),
            "note": "正文有改动（%d→%d 字）" % (old.get("detail_chars") or 0, new.get("detail_chars") or 0),
            "diff": lines,
        })
    return events


# ----------------------------------------------------------------------------- 主流程

def acquire_lock():
    ensure_dirs()
    if os.path.exists(LOCK_PATH):
        age = time.time() - os.path.getmtime(LOCK_PATH)
        if age < 3600:
            return False
        os.remove(LOCK_PATH)
    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def release_lock():
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


def write_detail_text(rid, text):
    path = os.path.join(DETAIL_DIR, rid + ".txt")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_detail_text(rid):
    path = os.path.join(DETAIL_DIR, rid + ".txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def run_cycle(cfg, args):
    ensure_dirs()
    state = load_json(STATE_PATH, {"version": 1, "items": {}, "detail_sweep_ts": 0})
    old_items = state.get("items") or {}
    years = resolve_years(cfg)
    sweep_due = (time.time() - float(state.get("detail_sweep_ts") or 0)) > cfg["detail_sweep_hours"] * 3600

    print("[%s] 抓取列表：年度 %s，标签 %s" % (iso(), ",".join(years), cfg["tabs"]))
    listed = {}
    for y in years:
        try:
            got = fetch_list(y, cfg)
        except Exception as e:  # noqa: BLE001
            print("  ! 年度 %s 列表抓取失败：%s" % (y, e), file=sys.stderr)
            continue
        print("  年度 %s：%d 个赛事" % (y, len(got)))
        listed.update(got)

    if not listed:
        raise RuntimeError("所有年度的列表都为空，疑似接口变更或网络异常；本轮不覆盖快照")

    # 决定要抓详情的赛事：新增的、列表字段有变化的、或到了全量扫描时间
    to_detail = []
    for rid, item in listed.items():
        prev = old_items.get(rid)
        if prev is None or sweep_due or args.full_detail:
            to_detail.append(rid)
            continue
        for field in ("name", "startTime", "endTime", "synopsis", "year", "coverUrl", "tabs"):
            if prev.get(field) != item.get(field):
                to_detail.append(rid)
                break

    print("[%s] 需要抓详情：%d 个%s" % (iso(), len(to_detail), "（含全量正文扫描）" if sweep_due else ""))
    details = {}

    def work(rid):
        time.sleep(cfg["request_delay_sec"])
        return rid, fetch_detail(rid, cfg)

    if to_detail:
        with futures.ThreadPoolExecutor(max_workers=max(1, int(cfg["detail_workers"]))) as pool:
            for rid, d in pool.map(work, to_detail):
                details[rid] = d

    # 合并出新的快照
    new_items = {}
    events = []
    old_texts, new_texts = {}, {}
    for rid, base in listed.items():
        prev = old_items.get(rid) or {}
        if rid in details:
            picked, text = pick_detail_fields(details[rid])
            old_texts[rid] = read_detail_text(rid)   # 必须在覆盖前读取上一版正文
            new_texts[rid] = text
            write_detail_text(rid, text)
            merged = dict(prev)
            merged.update(picked)
            merged["tabs"] = base["tabs"]
            merged["year"] = base.get("year") or merged.get("year")
            merged["createTime"] = base.get("createTime")
            merged["detail_checked"] = iso()
            new_items[rid] = merged
        else:
            merged = dict(prev)
            merged.update({k: v for k, v in base.items() if k != "id"})
            merged["id"] = rid
            new_items[rid] = merged

    # 生成事件
    for rid, item in new_items.items():
        prev = old_items.get(rid)
        name = item.get("name") or rid
        if prev is None:
            events.append({
                "type": "new", "field": None, "label": "新增赛事", "old": None, "new": name,
                "note": "新增赛事：%s（%s ~ %s）" % (name, item.get("startTime"), item.get("endTime")),
            })
            continue
        old_text = old_texts.get(rid) or read_detail_text(rid)
        new_text = new_texts.get(rid) or read_detail_text(rid)
        if rid not in details:
            # 未重新抓正文时不存在正文差异，跳过 detail 比较以避免误报
            prev_cmp = dict(prev)
            prev_cmp["detail_hash"] = item.get("detail_hash")
            prev = prev_cmp
        for ev in compare_item(prev, item, old_text, new_text):
            ev.update({"id": rid, "name": name, "ts": iso()})
            events.append(ev)

    for rid, prev in old_items.items():
        if rid not in new_items:
            still_live = None
            try:
                d = fetch_detail(rid, cfg)
                still_live = bool(d)
            except Exception:  # noqa: BLE001
                still_live = None
            if still_live is True:
                state_desc = "已从列表下架，但详情页仍可访问（多为报名截止/归档）"
            elif still_live is False:
                state_desc = "详情页也已不可用（疑似删除）"
            else:
                state_desc = "无法确认详情页状态（网络异常）"
            events.append({
                "type": "removed", "field": None, "label": "赛事从列表消失",
                "old": prev.get("name"), "new": None,
                "note": "赛事从列表消失：%s —— %s" % (prev.get("name") or rid, state_desc),
                "id": rid, "name": prev.get("name"), "ts": iso(),
            })

    # 写回快照
    state["items"] = new_items
    state["updated"] = iso()
    if to_detail:
        state["detail_sweep_ts"] = time.time()
    save_json(STATE_PATH, state)

    baseline = not old_items and not os.path.exists(EVENTS_PATH)
    return events, state, baseline, len(details)


def append_outputs(events, cfg, quiet=False):
    if not events:
        return
    with open(EVENTS_PATH, "a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    lines = ["", "## %s  共 %d 条变更" % (iso(), len(events)), ""]
    for ev in events:
        head = "- **[%s] %s** — %s" % (ev.get("type"), ev.get("label") or "", ev.get("note") or "")
        lines.append(head)
        if ev.get("diff"):
            lines.append("")
            lines.append("  ```diff")
            for d in ev["diff"]:
                lines.append("  " + d)
            lines.append("  ```")
    lines.append("")
    with open(CHANGES_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    if not quiet:
        for ev in events:
            print("  * [%s] %s" % (ev.get("type"), ev.get("note")))
            for d in ev.get("diff") or []:
                print("      " + d)
        print("  已写入 %s 与 %s" % (CHANGES_PATH, EVENTS_PATH))

    cmd = (cfg.get("notify_command") or "").strip()
    if cmd:
        title = "创赛平台 %d 条变更" % len(events)
        text = events[0].get("note") or title
        try:
            os.system(cmd.replace("{title}", title).replace("{text}", text))
        except Exception:  # noqa: BLE001
            pass


def cmd_once(cfg, args):
    events, state, baseline, ndetail = run_cycle(cfg, args)
    if baseline:
        print("[%s] 已建立基线：%d 个赛事（%d 个抓了详情），之后的变化会记为事件。"
              % (iso(), len(state["items"]), ndetail))
        return 0
    if not events:
        print("[%s] 无变化（跟踪 %d 个赛事）" % (iso(), len(state["items"])))
        return 0
    print("[%s] 发现 %d 条变化：" % (iso(), len(events)))
    append_outputs(events, cfg, quiet=args.quiet)
    return 0


def cmd_loop(cfg, args):
    interval = max(1, int(args.interval or cfg["interval_minutes"])) * 60
    print("[%s] 进入循环监听，每 %d 分钟一轮（Ctrl+C 退出）" % (iso(), interval // 60))
    while True:
        try:
            cmd_once(cfg, args)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001
            print("[%s] 本轮失败：%s" % (iso(), e), file=sys.stderr)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[%s] 已退出" % iso())
            return 0


def cmd_status(cfg, args):
    state = load_json(STATE_PATH, {})
    items = state.get("items") or {}
    print("快照时间：%s" % (state.get("updated") or "无"))
    print("跟踪赛事：%d 个" % len(items))
    by_year = {}
    for it in items.values():
        by_year[it.get("year") or "?"] = by_year.get(it.get("year") or "?", 0) + 1
    for y in sorted(by_year):
        print("  %s 年：%d" % (y, by_year[y]))
    if os.path.exists(EVENTS_PATH):
        n = sum(1 for _ in open(EVENTS_PATH, "r", encoding="utf-8"))
        print("历史事件：%d 条（%s）" % (n, EVENTS_PATH))
    print("变更报告：%s" % CHANGES_PATH)
    return 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    p = argparse.ArgumentParser(description="辽宁省创赛平台赛事变更监听")
    p.add_argument("--once", action="store_true", help="只跑一轮（默认）")
    p.add_argument("--loop", action="store_true", help="循环监听")
    p.add_argument("--status", action="store_true", help="查看当前快照统计")
    p.add_argument("--interval", type=int, default=0, help="循环模式间隔分钟数")
    p.add_argument("--years", default="", help="覆盖年度，逗号分隔，如 2026,2025")
    p.add_argument("--full-detail", action="store_true", help="本轮强制抓取全部详情正文")
    p.add_argument("--quiet", action="store_true", help="不打印每条事件明细")
    args = p.parse_args()

    cfg = load_config()
    if args.years:
        cfg["years"] = [y.strip() for y in args.years.split(",") if y.strip()]

    if args.status:
        return cmd_status(cfg, args)

    if not acquire_lock():
        print("[%s] 另一个实例正在运行（%s），本轮跳过" % (iso(), LOCK_PATH), file=sys.stderr)
        return 2
    try:
        if args.loop:
            return cmd_loop(cfg, args)
        return cmd_once(cfg, args)
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
