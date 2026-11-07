#!/usr/bin/env python3
"""HarmonyOS HAP 应用广告行为审计（v4：应用行为优先）。

审计对象是"应用做了什么"，不是"SDK 内部怎么实现"：
  - 行为层（重点）：广告位/场景、触发时机、密度与频控、用户可关闭项、
    数据采集、变现策略；
  - 能力层（附录）：所选 SDK 内部实现了哪些点击机制（穿透/CNR/force/auto），
    只在报告末尾作为"依赖的 SDK 能力"呈现。

产出:
  <out>/unzip/  <out>/scan.json  <out>/report.md
  --discover 追加 <out>/discover.md
"""
import argparse
import bisect
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

RUN = re.compile(rb"[\x20-\x7e\x80-\xff]{4,}")
CJK = re.compile(r"[\u4e00-\u9fff]")
HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"

APP_MARKERS = ["entry/src/main/ets", "@ad/ad", "app/src/main/ets", "app|app|"]

APP_CLASSES = [
    "SplashAdAggregateService", "InterstitialAdController", "InterstitialAdAggregateService",
    "HotStartAdController", "AdDisplayController", "AdvertAuctionCoordinator",
    "AdvertSourcePolicy", "AlbumPreviewInterstitialTracker", "IncentiveAdFreeManager",
    "RewardedAdManager", "FeedAdPlatformView", "AdController", "AdAdnController",
    "AdvertSourceBindings", "SplashAdConditionManager", "GromoreSplashAdManager",
    "GromoreController", "AdService",
]
TRIGGERS = ["onForeground", "onAppStateChanged", "onPageChange", "onFirstPageChange",
            "onDidShow", "onCameraFirstFrame", "onFirstFrame"]
USER_SWITCHES = ["个性化广告", "摇一摇", "关闭广告", "免广告", "去广告"]
DENSITY = [
    "adStrategy.json", "totalTimes", "intervalSkipNum", "advertisingNum",
    "hotStartAdTime", "maybeShowHotStart", "HotStartAdController",
    "checkAndShowHotSplashAd", "hot_ad_stay_in_background_seconds",
    "ad_splashAdExposureInterval", "splashAdTotalCount", "jingHongProportion",
]
SCENES = ["开屏", "插屏", "激励", "信息流", "热启", "冷启", "免广告", "跳过", "关闭按钮"]
SCENE_REQ = {
    "跳过": r"跳过.{0,6}(广告|开屏)|(广告|开屏).{0,6}跳过",
    "冷启": r"冷启.{0,6}(广告|开屏)|cold",
    "热启": r"热启.{0,6}(广告|开屏)|hot.?start",
}
PRIVACY = [
    "installedApp", "TAG_APP_LIST", "application-list-logstore", "getImei",
    "getMac", "READ_PASTEBOARD", "APP_TRACKING_CONSENT", "LTAI5t", "AccessKey",
]


def extract_strings(path):
    data = path.read_bytes()
    out = []
    for m in RUN.finditer(data):
        try:
            out.append(m.group().decode("utf-8"))
        except UnicodeDecodeError:
            continue
    return out


class Corpus:
    def __init__(self, lines):
        self.lines = lines
        self.big = "\n".join(lines)
        self.starts = [0]
        for l in lines:
            self.starts.append(self.starts[-1] + len(l) + 1)
        self.tok_owner = {}
        self.owner_fn = None

    def build_owner_index(self):
        """为裸标识符（如 getButtonPassPercent）找归属：看它出现在哪个文件路径下。"""
        from collections import Counter, defaultdict
        idx = defaultdict(Counter)
        tok_re = re.compile(r"[A-Za-z_][A-Za-z0-9_]{5,}")
        for ln in self.lines:
            if len(ln) > 600:
                continue
            o = self.owner_fn(ln) if self.owner_fn else "unknown"
            if o == "unknown":
                continue
            for t in set(tok_re.findall(ln)):
                idx[t][o] += 1
        self.tok_owner = idx

    def resolve(self, text):
        o = self.owner_fn(text) if self.owner_fn else "unknown"
        if o != "unknown":
            return o
        toks = re.findall(r"[A-Za-z_][A-Za-z0-9_]{5,}", text)
        for t in sorted(toks, key=len, reverse=True):
            c = self.tok_owner.get(t)
            if c:
                return c.most_common(1)[0][0]
        return "unknown"

    def line_of(self, pos):
        return bisect.bisect_right(self.starts, pos) - 1

    def matches(self, pattern, limit=3, flags=re.I, require=None, reject=None):
        rx = re.compile(pattern, flags)
        rq = re.compile(require, re.I) if require else None
        rj = re.compile(reject, re.I) if reject else None
        out, seen = [], set()
        for m in rx.finditer(self.big):
            i = self.line_of(m.start())
            if i < 0 or i >= len(self.lines):
                continue
            raw = self.lines[i]
            if len(raw) > 600:
                continue
            if rq and not rq.search(raw):
                continue
            if rj and rj.search(raw):
                continue
            s = raw.strip()[:200]
            if not s or s in seen:
                continue
            seen.add(s)
            out.append((i + 1, s))
            if len(out) >= limit:
                break
        return out

    def find(self, pattern, limit=20, flags=re.I):
        rx = re.compile(pattern, flags)
        out, seen = [], set()
        for m in rx.finditer(self.big):
            i = self.line_of(m.start())
            if i < 0 or i >= len(self.lines):
                continue
            raw = self.lines[i]
            if len(raw) > 600:
                continue
            val = m.group(0).strip()
            if not val or val in seen:
                continue
            seen.add(val)
            out.append((i + 1, val, raw.strip()[:160]))
            if len(out) >= limit:
                break
        return out

    def find_group(self, pattern, group=1, limit=20, flags=re.I):
        rx = re.compile(pattern, flags)
        out, seen = [], set()
        for m in rx.finditer(self.big):
            i = self.line_of(m.start())
            if i < 0 or i >= len(self.lines):
                continue
            raw = self.lines[i]
            if len(raw) > 600:
                continue
            val = m.group(group).strip()
            if not val or val in seen:
                continue
            seen.add(val)
            out.append((i + 1, val, raw.strip()[:160]))
            if len(out) >= limit:
                break
        return out

    def contains(self, kw, limit=3):
        out = []
        for i, s in enumerate(self.lines):
            if len(s) > 600:
                continue
            if kw in s:
                out.append((i + 1, s.strip()[:200]))
                if len(out) >= limit:
                    break
        return out


def best(cands, n=2):
    if not cands:
        return []
    scored = sorted(cands, key=lambda x: (bool(CJK.search(x[1])), len(x[1])), reverse=True)
    out, seen = [], set()
    for ln, s in scored:
        if s[:60] in seen:
            continue
        seen.add(s[:60])
        out.append((ln, s))
        if len(out) >= n:
            break
    return out


def load_json(name, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hap")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--dump-strings", action="store_true")
    ap.add_argument("--discover", action="store_true")
    args = ap.parse_args()

    hap = Path(args.hap).expanduser().resolve()
    if not hap.exists():
        sys.exit(f"not found: {hap}")
    out = Path(args.out) if args.out else Path.cwd() / f"adscan_{hap.stem}"
    out.mkdir(parents=True, exist_ok=True)
    unz = out / "unzip"
    if not unz.exists():
        with zipfile.ZipFile(hap) as z:
            z.extractall(unz)

    module = {}
    if (unz / "module.json").exists():
        module = json.loads((unz / "module.json").read_text(encoding="utf-8"))
    app, mod = module.get("app", {}), module.get("module", {})
    bundle = app.get("bundleName", "?")
    perms = [p.get("name") for p in mod.get("requestPermissions", [])]
    schemes = mod.get("querySchemes", [])

    lines = []
    for f in sorted(unz.rglob("*.abc")):
        lines.extend(extract_strings(f))
    raw_lines = set()
    for f in list(unz.rglob("resources/rawfile/*.js")) + list(unz.rglob("resources/rawfile/*.json")):
        if f.stat().st_size <= 30 * 1024 * 1024:
            try:
                for l in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                    lines.append(l)
                    raw_lines.add(l)
            except Exception:
                pass
    corpus = Corpus(lines)
    corpus.raw_lines = raw_lines

    if args.dump_strings:
        (out / "strings").mkdir(exist_ok=True)
        (out / "strings" / "all.txt").write_text("\n".join(lines), encoding="utf-8")

    libs = {p.name for p in (unz / "libs").rglob("*.so")}
    vendors = load_json("vendors.json", {"vendors": []})["vendors"]
    mechs = load_json("mechanisms.json", {"mechanisms": []})["mechanisms"]

    # 归属判定：应用代码 vs SDK 内部
    sdk_markers = []
    for v in vendors:
        for t in v.get("strings", []) + v.get("libs", []):
            sdk_markers.append(t.lower())

    def owner(line):
        if line in getattr(corpus, "raw_lines", set()):
            return "app"
        low = line.lower()
        if any(m in low for m in sdk_markers):
            return "sdk"
        if any(m in line for m in APP_MARKERS):
            return "app"
        return "unknown"

    corpus.owner_fn = owner
    corpus.build_owner_index()

    # ---------- 厂商 ----------
    vendor_rows = []
    for v in vendors:
        lib_hit = [l for l in v.get("libs", []) if l in libs]
        str_hit = [k for k in v.get("strings", []) if corpus.contains(k, 1)]
        if not lib_hit and not str_hit:
            continue
        vers = []
        for pat in v.get("version", []):
            m = corpus.matches(pat, 1)
            if m:
                mm = re.search(pat, m[0][1])
                if mm:
                    vers.append(mm.group(1))
        if v["name"] == "GroMore" and vers:
            vers = [f"appId={x}" for x in vers]
        vendor_rows.append({
            "name": v["name"], "role": v.get("role", ""),
            "versions": list(dict.fromkeys(vers)),
            "evidence": ", ".join(lib_hit + str_hit[:3]),
            "confidence": "高" if lib_hit or v["name"] in ("Onemore", "TopOn / Taku", "OSET（shenshi）") else "中",
        })

    # ---------- 应用行为 ----------
    def app_hits(words, limit=6):
        res = {}
        for w in words:
            c = [x for x in corpus.contains(w, 6) if owner(x[1]) != "sdk"]
            if c:
                res[w] = best(c, 1)
        return res

    orchestration = app_hits(APP_CLASSES, 12)
    trigger_hits = app_hits(TRIGGERS, 8)
    switches = app_hits(USER_SWITCHES, 6)
    privacy = app_hits(PRIVACY, 8)

    dens = {}
    for k in DENSITY:
        c = corpus.contains(k, 4)
        if c:
            dens[k] = best(c, 1)

    scenes = {}
    for k in SCENES:
        if k in SCENE_REQ:
            c = corpus.matches(SCENE_REQ[k], 8)
        else:
            c = corpus.contains(k, 8)
        if c:
            scenes[k] = best(c, 1)

    # ---------- 应用侧具体资产 ----------
    # 广告位 ID / 常量
    place = []
    for pat in [r"\bTAKU_[A-Z0-9_]*_ID\b",
                r"\b[A-Z][A-Z0-9_]{2,}_(?:SPLASH|INTERSTITIAL|REWARD|FEED|NATIVE|BANNER)[A-Z0-9_]*_ID\b"]:
        for _, val, ctx in corpus.find(pat, 10):
            val = re.sub(r"^[A-Z](?=TAKU_)", "", val)
            place.append(val)
    for pat in [r"placementId[ \t]*[=:][ \t]*[\"']?([A-Za-z0-9_-]{6,})",
                r"posId[ \t]*[=:][ \t]*[\"']?([A-Za-z0-9_-]{6,})"]:
        for _, val, ctx in corpus.find_group(pat, 1, 10):
            if re.search(r"(?i)imp|session|option|network|type$", val):
                continue
            place.append(val)
    hexids = []
    seen_hex = set()
    for i, l in enumerate(lines):
        if not re.search(r"TAKU|placement|posId|advert|_ID", l, re.I):
            continue
        for j in range(max(0, i - 4), min(len(lines), i + 5)):
            v = lines[j].strip()
            if re.fullmatch(r"[a-f0-9]{12,64}", v) and v not in seen_hex:
                seen_hex.add(v)
                hexids.append(f"{v}  ←  {l.strip()[:100]}")
                break
        if len(hexids) >= 6:
            break
    placements = list(dict.fromkeys(place))[:12] + hexids[:6]

    # 私有协议 / 配置端点
    endpoints = [v for _, v, _ in corpus.find(r"/mkg/[A-Za-z0-9_/]+", 40)]
    endpoints = list(dict.fromkeys(endpoints))[:15]

    # 门控/拦截原因
    reasons = Counter(re.findall(r"reason=([a-z0-9_]+)", corpus.big)).most_common(12)
    gate_tokens = sorted({t for t in re.findall(r"\b(?:switch_off|gate_rejected|config_mismatch|sdk_empty|stale_request|position_mismatch|rom_below_[a-z0-9_]*)\b", corpus.big)})

    # 去广告路径
    adfree = []
    for k in ["RemoveAdDialog", "IncentiveAdFreeManager", "免广告", "去广告", "AdFree"]:
        c = corpus.contains(k, 3)
        if c:
            adfree.append({"kw": k, "line": best(c, 1)[0][0], "text": best(c, 1)[0][1]})

    # 多套配置（GroMore 等）
    configs = []
    for f in unz.rglob("site_config_*"):
        configs.append(str(f.relative_to(unz)))
    for h in corpus.matches(r"site_config_\d+[A-Za-z0-9_.-]*", 10):
        configs.append(h[1])
    configs = list(dict.fromkeys(configs))[:8]

    # 未收录 SDK 候选（包注解）
    known_tokens = set()
    for v in vendors:
        for t in v.get("strings", []) + v.get("libs", []):
            t = t.lower().lstrip("@")
            known_tokens.update({t, t.split("/")[0], t.split(".")[0]})
    cand_all = []
    for (pkg, ver), c in Counter(re.findall(r"([@A-Za-z0-9_./-]{5,60})\|(\d+\.\d+\.\d+)", corpus.big)).most_common(300):
        low = pkg.lower().lstrip("@")
        root = low.split("/")[0]
        if root in {"entry", "camera", "app", "core", "map", "ad", "common", "feature", "main", "base", "ui", "utils", "components"}:
            continue
        if any(tok and (tok == root or tok in low) for tok in known_tokens):
            continue
        adlike = bool(re.search(r"ad|adv|adn|dsp|ssp|promote", low)) and not re.search(r"adapter|loader|helper", low)
        cand_all.append((adlike, c, pkg, ver))
    cand_all.sort(key=lambda x: (x[0], x[1]), reverse=True)
    candidates = [f"{p} ({v})" for _, _, p, v in cand_all[:10]]

    # 应用侧广告代码面
    app_paths = Counter()
    path_rx = re.compile(r"(?:entry|@ad/ad)/src/main/ets/[A-Za-z0-9_./-]+")
    for l in lines:
        if "/src/main/ets/" not in l:
            continue
        for m in path_rx.finditer(l):
            pth = m.group(0).rstrip("./-")
            if "/test/" in pth:
                continue
            if re.search(r"/ad/|modules/ad", pth):
                app_paths[pth] += 1
    app_files = len(app_paths)

    enable_ev = []
    for pat in [r"blocked by[^\n]{0,60}", r"switchOn=false[^\n]{0,40}", r"skip (?:cold|hot) splash[^\n]{0,40}",
                r"reason=ad_free_active[^\n]{0,40}"]:
        for _, val, ctx in corpus.find(pat, 6):
            enable_ev.append(val.strip())
    enable_ev = list(dict.fromkeys(enable_ev))[:8]

    strategies = []
    for sp in unz.rglob("adStrategy.json"):
        try:
            strategies.append({"path": str(sp.relative_to(unz)), "data": json.loads(sp.read_text(encoding="utf-8"))})
        except Exception:
            strategies.append({"path": str(sp.relative_to(unz)), "data": sp.read_text(encoding="utf-8", errors="ignore")[:1500]})

    # 密度评分（应用行为）
    density_score = 0
    if any(k in orchestration for k in ("SplashAdAggregateService", "GromoreSplashAdManager", "GromoreController", "AdService")):
        density_score += 1
    if any(k in orchestration for k in ("HotStartAdController", "maybeShowHotStart", "checkAndShowHotSplashAd")) or \
       any(k in dens for k in ("HotStartAdController", "maybeShowHotStart", "checkAndShowHotSplashAd")):
        density_score += 1
    for s in strategies:
        try:
            if any(int(x.get("totalTimes", 0)) >= 3 for x in s["data"]):
                density_score += 1
                break
        except Exception:
            pass
    if sum(1 for k in ["照片", "编辑", "拼图", "相册", "分享", "工作汇报"] if corpus.contains(k, 1)) >= 2:
        density_score += 1
    if orchestration.get("IncentiveAdFreeManager"):
        density_score += 1
    if orchestration.get("FeedAdPlatformView"):
        density_score += 1
    if any(k in dens for k in ("advertisingNum", "intervalSkipNum")):
        density_score += 1
    if any(x["kw"] in ("RemoveAdDialog", "去广告") for x in adfree):
        density_score += 1
    density_score = min(density_score, 5)
    density_level = "高" if density_score >= 4 else ("中" if density_score >= 2 else "低")

    # ---------- 机制（能力层） ----------
    mech_rows = []
    for m in mechs:
        score, generic_ev, vendor_ev = 0, [], []
        for g in m.get("generic", []):
            hit = corpus.matches(g["re"], 1, require=g.get("require"), reject=g.get("reject"))
            if hit:
                score += g.get("w", 1)
                generic_ev.append({"why": g.get("why", g["re"]), "line": hit[0][0],
                                   "text": hit[0][1], "owner": corpus.resolve(hit[0][1])})
        for g in m.get("vendor", []):
            hit = corpus.matches(g["re"], 1)
            if hit:
                score += g.get("w", 1)
                vendor_ev.append({"vendor": g.get("vendor"), "line": hit[0][0],
                                  "text": hit[0][1], "owner": corpus.resolve(hit[0][1])})
        score = min(score, 10)
        if score == 0:
            continue
        app_n = sum(1 for e in generic_ev + vendor_ev if e["owner"] == "app")
        sdk_n = sum(1 for e in generic_ev + vendor_ev if e["owner"] == "sdk")
        scope = "应用层接线" if app_n else "仅 SDK 内部"
        level = "强" if score >= 6 else ("中" if score >= 3 else "弱")
        mech_rows.append({"id": m["id"], "name": m["name"], "score": score, "level": level,
                          "app_n": app_n, "sdk_n": sdk_n, "scope": scope,
                          "generic": generic_ev, "vendor": vendor_ev})
    mech_rows.sort(key=lambda r: (r["scope"] == "应用层接线", r["score"]), reverse=True)

    trick_ids = {"pass_through", "close_click", "force_click", "auto_click"}
    by_id = {r["id"]: r for r in mech_rows}
    cap_high = any(by_id.get(i, {}).get("level") == "强" for i in trick_ids)
    behavior_risk = {"高": 3, "中": 2, "低": 1}[density_level]
    privacy_score = len(privacy)
    if privacy_score >= 3:
        behavior_risk = min(3, behavior_risk + 1)
    behavior_level = {3: "高", 2: "中", 1: "低"}[behavior_risk]

    scan = {
        "hap": str(hap), "bundle": bundle,
        "versionName": app.get("versionName"), "versionCode": app.get("versionCode"),
        "permissions": perms, "querySchemes": schemes,
        "vendors": vendor_rows, "mechanisms": mech_rows,
        "behavior": {"orchestration": orchestration, "triggers": trigger_hits,
                     "switches": switches, "privacy": privacy,
                     "density": dens, "scenes": scenes, "strategies": strategies,
                     "placements": placements, "endpoints": endpoints,
                     "reasons": reasons, "gate": gate_tokens,
                     "adfree": adfree, "configs": configs,
                     "candidates": candidates, "appFiles": app_files,
                     "enableEvidence": enable_ev,
                     "densityScore": density_score, "densityLevel": density_level,
                     "behaviorLevel": behavior_level},
        "capability": {"high": cap_high},
        "adStrategy": strategies,
    }
    (out / "scan.json").write_text(json.dumps(scan, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- 报告 ----------
    md = [f"# {bundle} 应用广告行为审计（初筛）\n\n",
          "> 审计对象是**应用行为**：投了哪些广告位、何时触发、密度多大、用户能不能关、"
          "采集了什么数据。SDK 内部实现放在附录，只作为「依赖的能力」。\n"
          "> 由 `scan_hap.py` 生成，需人工复核；**未发现 ≠ 不存在**。\n\n",
          "## 0. 摘要\n\n",
          f"- 广告 SDK：**{len(vendor_rows)}** 家（附录 B）\n",
          f"- 广告密度：**{density_level}**（{density_score}/5）\n",
          f"- 应用行为风险：**{behavior_level}**；依赖的 SDK 点击能力："
          f"**{'高' if cap_high else '中/低'}**（附录 A）\n",
          f"- 隐私采集线索：{len(privacy)} 项\n\n"]

    md += ["## 1. 样本与方法\n\n| 项 | 值 |\n|---|---|\n",
           f"| 文件 | `{hap.name}` |\n| 包名 | `{bundle}` |\n",
           f"| 版本 | {app.get('versionName')} ({app.get('versionCode')}) |\n",
           f"| 权限 / querySchemes | {len(perms)} / {len(schemes)} |\n",
           "| 方法 | HAP 解包 + ArkTS 字符串/符号分析；应用行为与 SDK 能力分开归因 |\n\n"]

    md += ["## 2. 应用行为总览\n\n| 维度 | 发现 |\n|---|---|\n",
           f"| 广告位编排 | {', '.join(list(orchestration)[:6]) or '未识别'} |\n",
           f"| 触发时机 | {', '.join(list(trigger_hits)[:5]) or '未识别'} |\n",
           f"| 密度 | {density_level}（{density_score}/5） |\n",
           f"| 用户可关闭项 | {', '.join(list(switches)[:5]) or '未发现'} |\n",
           f"| 数据采集 | {', '.join(list(privacy)[:5]) or '未发现'} |\n",
           f"| 应用广告代码 | {app_files} 个 .ets 文件（含 /ad/） |\n",
           f"| 广告位 ID | {len(placements)} 个（见 3.1.1） |\n",
           f"| 配置/协议端点 | {len(endpoints)} 个（见 3.4） |\n",
           f"| 未收录 SDK 候选 | {', '.join(candidates[:3]) or '无'} |\n\n"]

    md += ["## 3. 广告位与触发时机\n\n### 3.1 场景文案\n\n"]
    for k, v in scenes.items():
        md.append(f"- `{k}`：`{v[0][1]}`\n")
    md += ["\n### 3.1.1 广告位 ID / 常量\n\n"]
    for x in placements:
        md.append(f"- `{x}`\n")
    md += ["\n### 3.1.2 多套配置\n\n"]
    for c in configs:
        md.append(f"- `{c}`\n")
    md += ["\n### 3.2 应用层编排类\n\n"]
    for k, v in orchestration.items():
        md.append(f"- `{k}`：`{v[0][1]}`\n")
    md += ["\n应用侧广告相关文件 Top：\n\n"]
    for pth, cnt in app_paths.most_common(8):
        md.append(f"- `{pth}` ×{cnt}\n")
    md += ["\n### 3.3 触发时机\n\n"]
    for k, v in trigger_hits.items():
        md.append(f"- `{k}`：`{v[0][1]}`\n")

    md += ["\n### 3.4 配置与协议端点\n\n"]
    for e in endpoints:
        md.append(f"- `{e}`\n")
    md += ["\n### 3.5 门控与拦截原因\n\n"]
    if gate_tokens:
        md.append("- 枚举：" + ", ".join(f"`{g}`" for g in gate_tokens) + "\n")
    if reasons:
        md.append("- `reason=` Top：" + ", ".join(f"`{r}`×{c}" for r, c in reasons) + "\n")
    md += ["\n### 3.6 启用旁证（拦截/跳过日志）\n\n"]
    for e in enable_ev:
        md.append(f"- `{e}`\n")
    md.append("\n## 4. 广告密度\n\n")
    for s in strategies:
        md.append(f"`{s['path']}`:\n\n```json\n{json.dumps(s['data'], ensure_ascii=False, indent=2)[:1200]}\n```\n\n")
    for k, v in dens.items():
        md.append(f"- `{k}`：`{v[0][1]}`\n")

    md += ["\n## 5. 用户控制与合规\n\n### 5.1 用户可见开关/文案\n\n"]
    for k, v in switches.items():
        md.append(f"- `{k}`：`{v[0][1]}`\n")
    md += ["\n### 5.1.1 去广告路径\n\n"]
    for x in adfree:
        md.append(f"- `{x['kw']}`：`{x['text']}`（行 {x['line']}）\n")
    md += ["\n### 5.2 权限\n\n- " + ", ".join(f"`{p}`" for p in perms) + "\n"]
    rel = []
    if "ohos.permission.ACCELEROMETER" in perms or "ohos.permission.GYROSCOPE" in perms:
        rel.append("ACCELEROMETER/GYROSCOPE → 摇一摇/扭一扭")
    if any(p.startswith("ohos.permission.LOCATION") or "LOCATION" in p for p in perms):
        rel.append("LOCATION 系 → 广告定向")
    if "ohos.permission.READ_PASTEBOARD" in perms:
        rel.append("READ_PASTEBOARD → 剪贴板营销读取")
    if "ohos.permission.APP_TRACKING_CONSENT" in perms:
        rel.append("APP_TRACKING_CONSENT → OAID 追踪")
    if rel:
        md += ["\n广告相关权限：\n\n"] + [f"- {x}\n" for x in rel]

    md += ["\n### 5.3 数据采集线索\n\n"]
    for k, v in privacy.items():
        md.append(f"- `{k}` → `{v[0][1]}`\n")
    md += ["\n### 5.4 querySchemes\n\n- " +
           (", ".join(schemes[:30]) + ("…" if len(schemes) > 30 else "")) + "\n"]

    md += ["\n## 6. 风险判断\n\n| 维度 | 结论 | 依据 |\n|---|---|---|\n",
           f"| 广告密度 | {density_level} | 编排类/策略/场景/激励/信息流 |\n",
           f"| 用户影响 | {behavior_level} | 密度 + 用户可关闭项 + 数据采集 |\n",
           f"| 点击能力依赖 | {'高' if cap_high else '中/低'} | 所选 SDK 的机制（附录 A） |\n\n"]

    md += ["## 7. 待人工确认\n\n",
           "建议优先 grep（在 `strings/all.txt` 上）：\n\n",
           "```bash\n",
           "grep -nE 'switchOn=|blocked by|skip cold|skip hot' strings/all.txt | head -30\n",
           "grep -nE 'site_config_|mingwen' strings/all.txt | head -20\n",
           "grep -nE 'RemoveAdDialog|每天4分钱|免广告' strings/all.txt | head -20\n",
           "grep -nE 'TAKU_.*_ID|[a-f0-9]{12,64}' strings/all.txt | head -30\n",
           "```\n\n",
           "- [ ] 各广告位的实际开关与频次（真机抓配置响应）\n",
           "- [ ] 用户关闭摇一摇/个性化广告后的真实效果\n",
           "- [ ] SDK 是否初始化（打包 ≠ 启用）\n",
           "- [ ] 隐私政策是否披露安装列表 / IMEI / MAC\n",
           "- [ ] 明文密钥是否需要轮换\n\n"]

    md += ["## 附录 A：SDK 能力与点击机制（非重点）\n\n",
           "> 这些是**所选 SDK 内部**的实现，用于解释「点击率为什么能高」；"
           "是否启用取决于服务端配置，包内无法确认。\n\n",
           "| 机制 | 等级 | 应用层证据 | SDK 证据 | 归因 |\n|---|---|---|---|---|\n"]
    for r in mech_rows:
        md.append(f"| {r['name']} | {r['level']} | {r['app_n']} | {r['sdk_n']} | {r['scope']} |\n")
    md.append("\n### 逐条证据\n\n")
    for r in mech_rows:
        md.append(f"#### {r['name']}（{r['score']} / {r['level']} / {r['scope']}）\n\n")
        for e in r["generic"]:
            tag = "应用" if e["owner"] == "app" else ("SDK" if e["owner"] == "sdk" else "未知")
            md.append(f"- [{tag}] {e['why']}：`{e['text']}`（行 {e['line']}）\n")
        for e in r["vendor"]:
            md.append(f"- [SDK/{e['vendor']}] `{e['text']}`（行 {e['line']}）\n")
        md.append("\n")

    md += ["## 附录 B：SDK 指纹\n\n| 平台 | 角色 | 版本 | 置信度 | 证据 |\n|---|---|---|---|---|\n"]
    for r in vendor_rows:
        md.append(f"| {r['name']} | {r['role']} | {', '.join(r['versions']) or '-'} | {r['confidence']} | {r['evidence']} |\n")
    md += ["\n> 指纹库：`data/vendors.json`；未收录 SDK 用 `--discover` 发现。\n\n"]
    if candidates:
        md.append("未收录候选（弱证据，需人工判断）：" + "、".join(f"`{c}`" for c in candidates) + "\n\n")

    md += ["## 附录 C：复现\n\n```bash\n",
           f"python3 scan_hap.py {hap.name} --dump-strings --discover\n```\n"]
    (out / "report.md").write_text("".join(md), encoding="utf-8")

    # ---------- 发现模式 ----------
    if args.discover:
        dom = Counter(re.findall(r"https?://([A-Za-z0-9.-]+)", corpus.big))
        ann = Counter(re.findall(r"([@A-Za-z0-9_./-]{5,60})\|(\d+\.\d+\.\d+)", corpus.big))
        tokens = Counter(re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,40}", corpus.big))
        clickish = [(t, c) for t, c in tokens.items()
                    if re.search(r"click|pass|shake|twist|tilt|overlay|dispatch|landing|deeplink", t, re.I)]
        clickish.sort(key=lambda x: -x[1])
        dm = ["# 未知 SDK / 新机制发现\n\n", "> 先看域名与包注解，再看可疑标识符。\n\n",
              "## 域名 Top 40\n\n| 次数 | 域名 |\n|---|---|\n"]
        for d, c in dom.most_common(40):
            dm.append(f"| {c} | `{d}` |\n")
        dm += ["\n## 包注解 Top 40\n\n| 次数 | 包 | 版本 |\n|---|---|---|\n"]
        for (pkg, ver), c in ann.most_common(40):
            dm.append(f"| {c} | `{pkg}` | {ver} |\n")
        dm += ["\n## 点击相关标识符 Top 60\n\n| 次数 | 标识符 |\n|---|---|\n"]
        for t, c in clickish[:60]:
            dm.append(f"| {c} | `{t}` |\n")
        dm += ["\n## 可能是未收录 SDK 的注解\n\n"]
        known_tokens = set()
        for v in vendors:
            for t in v.get("strings", []) + v.get("libs", []):
                t = t.lower().lstrip("@")
                known_tokens.update({t, t.split("/")[0], t.split(".")[0]})
        ignore_roots = {"entry", "camera", "app", "core", "map", "ad", "common",
                        "feature", "main", "base", "ui", "utils", "components"}
        seen = set()
        for (pkg, ver), c in ann.most_common(300):
            low = pkg.lower().lstrip("@")
            root = low.split("/")[0]
            if root in seen or root in ignore_roots:
                continue
            if any(tok and (tok == root or tok in low) for tok in known_tokens):
                continue
            seen.add(root)
            dm.append(f"- `{pkg}` ({ver}) × {c}\n")
            if len(seen) >= 15:
                break
        (out / "discover.md").write_text("".join(dm), encoding="utf-8")
        print(f"[scan_hap] discover: {out/'discover.md'}")

    print(f"[scan_hap] report: {out/'report.md'}")
    print(f"[scan_hap] json  : {out/'scan.json'}")


if __name__ == "__main__":
    main()
