# harmonyos-ad-audit

> HarmonyOS HAP 应用广告行为审计 skill：识别广告位、触发时机、密度、用户控制、数据采集，
> 并把 SDK 内部机制降级为附录。输出 Markdown 与中文 PDF。
>
> **仅用于已获授权的应用安全研究与广告效果核验。** 使用前请阅读 [NOTICE.md](NOTICE.md)。

版本：1.1（2026-11-07）
适用：DSH / 任何能读取 `SKILL.md` 的 agent；脚本为独立 Python，可单独使用。

---

## 1. 安装

### DSH

把整个目录放进 skills 目录：

```bash
mkdir -p ~/.dsh/skills
tar -xzf harmonyos-ad-audit-*.tar.gz -C ~/.dsh/skills/
# 或：unzip harmonyos-ad-audit-*.zip -d ~/.dsh/skills/
```

重开一个会话，技能列表里会出现 `harmonyos-ad-audit`。

### 其他 agent 框架

把 `SKILL.md` 放到该框架的 skills 目录即可；`scripts/` 与 `data/` 保持同级。
脚本本身不依赖 DSH，只要有 Python 3.9+。

---

## 2. 依赖

| 依赖 | 必需 | 用途 |
|---|---|---|
| python3（3.9+） | 是 | 扫描脚本 |
| pandoc | 出 PDF 时 | Markdown → HTML |
| weasyprint | 出 PDF 时 | HTML → PDF（中文字体正常） |
| 中文字体 | 出 PDF 时 | 推荐 WenQuanYi / Noto Sans CJK |

只做扫描不需要 pandoc/weasyprint。

自检：

```bash
python3 -V && pandoc -V | head -1 && weasyprint --version && fc-list :lang=zh | head -1
```

---

## 3. 快速开始

```bash
SCAN=~/.dsh/skills/harmonyos-ad-audit/scripts

# 初筛：解包 + 字符串 + 应用行为 + SDK 指纹 + 未知发现
python3 $SCAN/scan_hap.py /path/app.hap --dump-strings --discover
# 产物：adscan_app/
#   unzip/  strings/all.txt  scan.json  report.md  discover.md

# 人工复核 report.md 的「待人工确认」后，出 PDF
python3 $SCAN/md2pdf.py report.md report.pdf \
  --title "某某 App 应用广告行为审计" \
  --subtitle "HarmonyOS HAP 静态分析" \
  --meta "包名=com.example" --meta "版本=1.0" \
  --meta "样本=app.hap / MD5 ..."

# 单文件提字符串（排查某个 .abc / .so）
python3 $SCAN/extract_strings.py unzip/ets/modules.abc -o abc.txt --with-offset
```

---

## 4. 报告结构（应用行为优先）

```
0 摘要            应用行为风险 / 依赖的 SDK 能力（分开写）
1 样本与方法
2 应用行为总览
3 广告位与触发时机
4 广告密度
5 用户控制与合规
6 风险判断
7 待人工确认
附录 A SDK 能力与点击机制（非重点，带归因列）
附录 B SDK 指纹
附录 C 证据与复现
```

归因规则：

| 归属 | 判定依据 | 写在哪 |
|---|---|---|
| 应用行为 | 路径含 `entry/src/main/ets`、`@ad/ad`、`app/src/main/ets` | 主报告 |
| SDK 内部 | 厂商包名（`@csj/openadsdk`、`ksadsdk`、`@shenshi/opensetsdk-core`…） | 附录 A |
| 无法判定 | 裸标识符 | 用 token→路径索引推断，仍未知标「未定」 |

**未发现 ≠ 不存在**；「能力存在」≠「已启用」。

---

## 5. 目录结构

```
harmonyos-ad-audit/
├── SKILL.md                 方法论（给 agent）
├── README.md                本文件
├── data/
│   ├── vendors.json         厂商指纹包（加 SDK 只改这里）
│   └── mechanisms.json      机制定义（generic 通用 + vendor 已知字段）
└── scripts/
    ├── scan_hap.py          初筛 + 应用行为/能力归因 + --discover
    ├── extract_strings.py   UTF-8 中文友好提取
    └── md2pdf.py            中文 PDF（封面/目录/页码）
```

---

## 6. 扩展

### 加一个新广告 SDK

编辑 `data/vendors.json`，追加：

```json
{
  "name": "某 SDK",
  "role": "聚合",
  "libs": ["libxxx.so"],
  "strings": ["com.xxx.sdk", "XxxAd"],
  "version": ["com\\.xxx\\.sdk\\|(\\d+\\.\\d+\\.\\d+)"],
  "domains": ["xxx.com"]
}
```

不需要改代码。

### 加一个新机制特征

编辑 `data/mechanisms.json` 对应机制的 `generic`（与厂商无关）或 `vendor`（已知字段），
支持字段：`re`、`w`（权重）、`why`、`require`（上下文必须匹配）、`reject`（命中则排除）。

评分：强 ≥6 / 中 3–5 / 弱 1–2（每家族只计一次）。

---

## 7. 已知限制

1. 静态为主：`forceClickEnable` 等实际下发值需真机抓 sort/配置响应。
2. 词语级识别：硬编码、混淆、无日志的实现可能漏。
3. HAP 不能装进 Android；`hdc list targets` 为空时只能静态分析。
4. 协议探测有红线：最多 3–5 次无身份请求，不爆破签名、不冒用媒体身份。
5. 中文正则依赖文案，厂商改文案会漏。

---

## 8. 版本

- 2026-11-07 v1.1：新增应用侧资产抽取（广告文件清单、广告位常量/十六进制位 ID、
  配置与协议端点、门控原因、启用旁证、去广告路径、多套配置、未收录候选）；
  权限→广告相关性映射；人审 grep 清单；广告位 ID 邻域提取。
- 2026-11-07 v1.0：应用行为优先的报告结构；归属判定（应用/SDK）；`--discover`；
  vendors/mechanisms 数据包；中文 PDF 生成。
