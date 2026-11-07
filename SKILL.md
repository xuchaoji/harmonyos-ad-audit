---
name: harmonyos-ad-audit
description: 扫描 HarmonyOS HAP 应用的广告接入并产出审计报告：识别广告平台/SDK 版本、广告类型与投放场景、广告密度（开屏/热启/插屏/激励/信息流、频次与间隔）、点击放大机制（穿透点击、关闭触发点击 CNR、强制点击、自动点击、摇一摇/扭一扭、滑动物料）、以及隐私与合规风险；输出 Markdown 与带封面目录页码的中文 PDF。
whenToUse: 用户要求分析/审计某个 .hap 或鸿蒙应用的广告接入、广告位、广告密度、点击率异常、广告合规、SDK 清单、"接了哪些广告平台"、"有没有骚操作"，或要求把审计结果整理成报告/PDF 时。
---

# HarmonyOS 应用广告审计

把「拆包 → 还原应用行为 → 判密度 → 查合规 → 出报告」固化成可重复流程。

**审计对象是应用行为，不是 SDK 内部实现。** 主报告回答：这个 App 投了哪些广告位、什么时候弹、
多频繁、用户能不能关、采集了什么数据。SDK 内部怎么实现点击，只在**附录 A**作为「依赖的能力」出现。

```
SKILL.md
scripts/scan_hap.py        自动初筛（厂商包 + 通用机制识别 + --discover）
scripts/extract_strings.py 中文友好字符串提取
scripts/md2pdf.py          中文 PDF（封面/目录/页码）
data/vendors.json          厂商指纹包（加 SDK 只改这里）
data/mechanisms.json       机制定义（generic 通用特征 + vendor 已知字段）
```

## 0. 三条铁律

1. **证据分级，能力 ≠ 启用**。包里出现 `forceClickEnable` 只说明「SDK 支持」，不代表线上开了。结论必须标注置信度：强（native so + 完整包注解 + 调用日志）/ 中（只有字符串）/ 弱（只有配置枚举）。
2. **不绕过鉴权**。协议层探测最多做 3–5 次**无身份**请求并如实记录；不要爆破签名、不要冒用媒体身份、不要解密他人数据。发现内置私钥/明文 AK 只作为安全缺陷记录。
3. **HAP 装不进 Android**。`hdc list targets` 为空时只能静态分析，报告里要写明「未做运行时验证」；不要用 adb 设备冒充鸿蒙设备。

## 0.5 通用性：先认机制，再认厂商（重要）

`forceClickEnable`、`canClosingTriggeredClick` 是 OSET 的字段名，**换一家 SDK 就不叫这个**；
甚至有厂商根本不给开关，直接硬编码。所以本 skill 的识别顺序是：

```
机制（与厂商无关） → 厂商（指纹/版本） → 未知发现（--discover） → 人工确认
```

**1）机制层**（`data/mechanisms.json`）用通用语义特征，例如：

| 机制 | 通用特征（换 SDK 也成立） |
|---|---|
| 穿透点击 | `passThrough` / `buttonPass` / 中文「穿透」 |
| 关闭触发点击 | `closing*click`、`close*trigger*click`、中文「关闭广告…点击」 |
| 强制点击 | `force*click`、中文「强制点击」 |
| 自动点击 | `auto*click`（排除 `antispam/autoclick detect` 这类反作弊词） |
| 传感器点击 | `shake/twist/tilt`、`accelerometer/gyroscope`、中文「摇一摇/扭一扭」 |
| 互动物料 | `slide*welfare`、`waterRipple`、`erase`、中文「滑动福利/水波纹」 |
| 点击分发链 | `ClickDispatcher/ClickHelper`、`reportAdClick`、`nurl/__CLICK`、`openLanding/deeplink` |
| 点击频控 | `passPercent`、`dailyLimit/每日上限`、`minInterval/最小间隔` |

厂商字段命中只作为**加分证据**，不作为识别前提；报告里会分开标注 `[通用]` 与 `[厂商]`。

**2）厂商层**（`data/vendors.json`）只负责「这是谁、什么版本」，新增 SDK 加一条即可，不改代码：

```json
{"name":"某 SDK","role":"聚合","libs":["libxxx.so"],
 "strings":["com.xxx.sdk","XxxAd"],
 "version":["com\\.xxx\\.sdk\\|(\\d+\\.\\d+\\.\\d+)"],
 "domains":["xxx.com"]}
```

**3）未知发现**（`--discover`）输出：域名 Top、包注解 `pkg|version` Top、点击相关标识符 Top、
以及「可能是未收录 SDK 的注解」。遇到新厂商时：先看域名归属，再把包注解/类名前缀补进 vendors.json。

**4）盲测协议**（每次改规则后必做）：
- 正样本：至少两个不同 SDK 组合的真实 App；
- 负样本：无广告的 HAP（应输出 0 家 / 0 机制 / 风险低）；
- 检查：新增命中是否来自「通用特征」，还是只靠厂商字段；证据行是否真的与广告相关（防 `CameraService` 这类子串误报）。

**5）措辞纪律**：
- 未命中 ≠ 不存在。报告必须写「未发现」而不是「没有」。
- 「能力存在」与「已启用」分开；厂商字段只证明能力。
- 通用特征的置信度天然低于厂商字段，评分里已体现（强 ≥6 / 中 3–5 / 弱 1–2）。

## 0.6 审计边界：应用行为 vs SDK 内部

| 归属 | 判定依据 | 写在哪 |
|---|---|---|
| **应用行为** | 路径含 `entry/src/main/ets`、`@ad/ad`、`app/src/main/ets`；应用自己的 Controller/Manager/Service；应用配置与文案 | **主报告**（必写） |
| **SDK 内部** | 路径含厂商包名（`@csj/openadsdk`、`ksadsdk`、`@shenshi/opensetsdk-core`…）；厂商字段名 | **附录 A**（非重点） |
| **无法判定** | 裸标识符，无路径 | 先按 0.5 的 token 归属推断；仍未知就标「未定」 |

规则：
1. 主报告只写「应用做了什么」；SDK 的实现细节不占主章节。
2. 机制只有在**应用层有接线证据**（应用代码/配置引用该机制）时才写进主报告；
   只有 SDK 内部证据时，统一进附录 A，并注明「是否启用取决于服务端配置，包内无法确认」。
3. 报告摘要只给两个结论：**应用行为风险**（密度/触发/用户控制/采集）与
   **依赖的 SDK 能力**（附录 A），不要混成一个分数。
4. 厂商内部实现再吓人，也不能直接等同于「这个 App 在作弊」；要写清归因。

## 1. 一分钟开始

```bash
SCAN=~/.dsh/skills/harmonyos-ad-audit/scripts

# 1) 自动初筛：解包 + 提字符串 + 指纹 + 证据聚合
python3 $SCAN/scan_hap.py /path/app.hap --dump-strings --discover
# 产物：adscan_<name>/{unzip/, strings/all.txt, scan.json, report.md, discover.md}

# 2) 人工复核 report.md 的「待人工确认」，补全结论

# 3) 出 PDF（封面/目录/页码，中文字体自动探测）
python3 $SCAN/md2pdf.py report.md report.pdf \
  --title "某某 App 广告审计" \
  --subtitle "HarmonyOS HAP 静态分析" \
  --meta "包名=com.example" --meta "版本=1.0" --meta "样本=app.hap / MD5 ..."
```

单文件提字符串（排查某个 .abc / .so）：

```bash
python3 $SCAN/extract_strings.py unzip/ets/modules.abc -o abc.txt --with-offset
```

## 2. 环境与依赖

| 依赖 | 用途 | 检查 |
|---|---|---|
| python3 | 脚本 | `python3 -V` |
| pandoc | Markdown→HTML | `pandoc -V` |
| weasyprint | HTML→PDF（CJK 正常） | `weasyprint --version` |
| 中文字体 | 避免豆腐块 | `fc-list :lang=zh` |
| unzip | 解包（脚本内置 zipfile，可不装） | — |

缺 weasyprint 时退化为「只出 Markdown + HTML」；不要为此阻塞分析。

## 3. 解包与字符串：三个坑

1. **`strings` 会截断中文**。ArkTS 的 `.abc` 里中文日志是 UTF-8，但 `strings` 只认 ASCII，`grep 广告` 会 0 命中。必须用 `extract_strings.py`（按「可打印 ASCII + 高位字节」切分后整体解码）。
2. **Flutter 应用的广告不在 Dart 里**。`libapp.so` 是 Dart AOT，中文常被压缩/编码；广告逻辑通常在 ArkTS 的 `ets/modules.abc`（本 skill 的两个案例都是这样）。
3. **配置在 rawfile**。优先看 `resources/rawfile/adStrategy.json`、`site_config_*`、`*.json`、`flutter_assets/`；`module.json` 里有包名、版本、权限、querySchemes。

`.abc` 是 PANDA 格式，没有反汇编器时只能做字符串+符号级分析；**不要声称看到了函数体**，用「字段/方法/日志存在」来表述。

### 3.1 自动抽取清单（v1.1）

`scan_hap.py` 现在会直接产出这些**应用侧证据**，不用再手工 grep：

- 应用侧广告代码面：`entry/@ad/ad` 下 `/ad/` 相关文件清单与出现次数
- 广告位：`TAKU_*_ID` 常量、`*_SPLASH_*_ID` 等常量、`placementId/posId`、邻域十六进制位 ID
- 配置与协议端点：`/mkg/Advertising/*` 等
- 门控原因：`switch_off / gate_rejected / config_mismatch / sdk_empty / stale_request / position_mismatch / rom_below_*`
- 启用旁证：`blocked by ... / switchOn=false / skip cold splash`
- 去广告路径：`RemoveAdDialog`、激励免广告、每日付费入口
- 多套配置：`site_config_*`、`*_mingwen`
- 未收录 SDK 候选（按"广告可疑度 + 出现次数"排序）
- 权限→广告相关性映射（加速度/陀螺仪、定位、剪贴板、OAID）
- 人审 grep 清单（直接复制执行）

## 4. 平台指纹（怎么认）

| 平台 | 强指纹 | 版本注解 | 角色 |
|---|---|---|---|
| 穿山甲 Pangle / CSJ | `libpanglearmor.so`、`@csj/openadsdk`、`CSJSplashAd` | `@csj/openadsdk\|7.x.x` | 直连 / GroMore |
| 优量汇 GDT | `libgdtunionsdk.so`、`libgdtjsvm.so`、`@gdt/gdt-union-sdk` | `@gdt/gdt-union-sdk\|1.x.x` | 直连 |
| 快手联盟 KSAd | `libksadsdk.so`、`libkwai-v8-lite.so`、`ksadsdk` | `ksadsdk\|3.x.x` | 直连 |
| 华为 AdsKit（鲸鸿动能） | `SystemCapability.Advertising.Ads`、`advertising.AutoAdComponent`、`JingHong` | 系统 API | 系统广告 |
| TopOn / Taku | `anythink_sdk`、`cn-api.anythinktech.com` | `anythink_sdk\|6.x.x` | 聚合 |
| Onemore | `@onemore/sdk`、`OnemoreSplashView` | `@onemore/sdk\|1.x.x` | 聚合 |
| GroMore | `GromoreManager`、`site_config_<appId>` | appId | 穿山甲聚合 |
| OSET（shenshi） | `@shenshi/opensetsdk-core`、`open-set-api.shenshiads.com` | `1.0.x` | 本地竞价 ADX |
| Mintegral / Sigmob / 倍孜 | 关键字 | — | 海外/补充 |

**判读规则**：
- 有 native `.so` + 完整包注解 → **高置信**（SDK 确实被编译进来）。
- 只有类名/域名 → **中**（可能是适配器、配置枚举或残留）。
- 出现 `NonStandardAdSourceBaidu/Beizi/...` 这类**枚举**，不等于接入了对应 SDK。

## 5. 广告类型与场景

**类型**（能力面）：Splash / Interstitial / Reward / Feed / Native / Draw / Banner。
**是否真用**：找应用层编排类，而不是 SDK 类名——
`SplashAdAggregateService`、`InterstitialAdController`、`RewardedAdManager`、`FeedAdPlatformView`、`AdDisplayController`、`HotStartAdController`。

**场景**（从中文日志/枚举还原）：
- 开屏：冷启、**热启**（回前台）、独立广告窗口、开屏底部激励
- 插屏：照片预览 / 编辑保存 / 拼图 / 相册选图 / 团队人数 / 分享微信
- 激励：免广告时长、会员权益、积分
- 信息流：Flutter PlatformView 或原生列表

**判「启用」的旁证**：`maybeShowXxx`、`loadXxxAd`、`showXxxAd` 的日志文案、`AdSwitch`/`adStrategy.json` 的开关字段。

## 6. 广告密度评估（0–5 分）

| 指标 | 观察点 | 加分 |
|---|---|---|
| 冷启开屏 | `requestColdSplashAd` / `cold splash` | +1 |
| 热启开屏 | `HotStartAdController`、`hotStartAdTime`、`maybeShowHotStart` | +1（高频回前台再 +1） |
| 每日多轮 | `adStrategy.json` 的 `totalTimes` / `intervals` | `totalTimes≥3` +1 |
| 插屏场景数 | 场景枚举/开关数量 | ≥3 个场景 +1 |
| 激励闭环 | `IncentiveAdFreeManager`、`rewarded` | +1 |
| 信息流 | `FeedAdPlatformView` | +1 |
| 开屏叠加 | 开屏上再挂「去广告/福利」入口 | +1 |

**输出**：`低（0–1）/ 中（2–3）/ 高（4+）`，并列出每个加分的证据行。

## 7. 点击机制：先判归属，再决定放哪（非主章节）

> 这一章是**证据归因方法**，不是主报告章节。应用层没有接线证据时，整章内容进附录 A。
> 下面列的字段名是「已知厂商的例子」，不是识别前提；识别逻辑见 0.5 与 `data/mechanisms.json`。

先记住结论：这类 SDK **不注入系统触摸**，而是「覆盖层拦截 + 直接调用点击分发链」。
验证方法：搜 `injectTouch` / `dispatchTouchEvent` / `simulate`，若只出现在 Flutter 键盘/地图 SDK，即可排除注入。

### 7.1 穿透点击（pass-through）

| 证据 | 说明 |
|---|---|
| `buttonPassThrough` / `passThrough` | 组件属性 |
| `getButtonPassPercent` / `SortRaw.getButtonPassPercent` | 服务端下发概率 |
| `recordPassClick`、`穿透放行`、`穿透拦截(开关未开启/已达当日上限/未到间隔)` | 频控与记账 |
| `按钮恢复可点击(广告点击回调)` | 点击后恢复按钮 |

### 7.2 关闭触发点击（CNR）

`can_closing_triggered_click` / `canClosingTriggeredClick`（逐条广告）、`isClickCallbackByCnr`、`TAG_CNR`、`cnr 第 N 次，最小间隔(ehl)=`。

### 7.3 强制点击 / 自动点击

`forceClickEnable` / `forceClickLimit`（fc/fcl/ehl）；`isAutoClick` / `isAutoDownload` / `auto_click`。

### 7.4 传感器与互动物料

- `Sld` 枚举：`SHAKE_IT`（摇）、`TILT_SENSOR`（倾斜）、`SWIPE_CLICK`（滑动）、`ERASE`（擦除）、`CUSTOM`
- `dispatchClickWithSld`、`twistValue`、`adx_api_action_nyn`、`摇一摇`、`扭动手机`
- `OSETSlideWelfare`（滑动打开福利，自动动画）、`OSETWaterRipple`（水波纹）、`OSETPassEffect`
- 关键文案：`滑动福利组件点击(非穿透), 触发关闭` —— 开启穿透后，点「关闭/福利」等于点广告

### 7.5 点击分发链（判断"模拟"的关键）

```
onTrigger/onTapClose → doAdClick() → ClickDispatcher.dispatchClickWithSld()
  ├─ reportClicks()        点击监测 URL（宏替换）
  ├─ openLanding()         落地页（openLink）
  ├─ openDeeplink()        拉起 App
  └─ openDownload()        应用市场
```

三方网络走 `BaseNativeBridge` / `adBridgeInterface`（快手侧明确 `reportAdClick`）。

**证据强度**：同时命中「字段 + 日志 + 分发类」= 强；只有字段 = 中。

**还原伪代码**（写进报告时标注「据日志还原」）：

```ts
onTouch(ev) {
  if (!buttonPassThrough) return;
  if (clickCounted) return;
  if (!gateAllow(posId)) return;                 // 日上限/间隔
  if (random() > passPercent) return;            // 概率
  if (sld === 'SHAKE_IT' && twist < twistValue) return;
  recordPassClick(posId); doAdClick(); clickCounted = true;
}
```

## 8. 合规与隐私

| 检查项 | 关键词 | 风险 |
|---|---|---|
| 安装列表 | `installedApp`、`TAG_APP_LIST`、`application-list-logstore`、`canOpenLink` | 高（需单独同意） |
| 设备标识 | `getImei`、`getMac`、`OAID`、`bootMark` | 高 |
| 剪贴板 | `READ_PASTEBOARD` | 中 |
| 追踪授权 | `APP_TRACKING_CONSENT` | 中（需拒绝路径） |
| 明文密钥 | `LTAI`、`AccessKey`、`SecretKey`、签名盐 | 高（安全） |
| 摇一摇无开关 | 只有 SDK 文案、无设置页 | 中（体验/误触） |

对照输出：**已披露 / 未披露 / 无法判断**，别直接下「违法」结论。

## 9. 协议层探测（可选，谨慎）

只在用户明确授权、且目标是自己/客户的应用时做：

1. 先 `curl -sS -D -` 看网关、CORS、错误码，**最多 5 次**。
2. 记录完整请求头/响应体（脱敏）。
3. 发现「响应 RSA 加密且私钥在包内」→ 记为**安全缺陷**，不要批量解密。
4. 需要业务字段（`forceClickEnable` 等）时，说明「需真机抓包」，给 mitmproxy 步骤，不要爆破签名。

真机步骤（写进报告附录）：

```bash
mitmproxy -p 8080            # 手机 WiFi 代理指向 PC
# 手机浏览器访问 mitm.it 安装并信任 CA
mitmdump -w app.flows -p 8080
# 过滤：~u open-set-api.example.com，重点看 /sequence/ 响应
```

## 10. 报告结构（模板）

```markdown
# <App> 应用广告行为审计
## 0. 摘要（应用行为风险 + 依赖的 SDK 能力，分开写）
## 1. 样本与方法（含局限：是否运行时验证）
## 2. 应用行为总览（投放面 / 触发 / 密度 / 用户控制 / 采集）
## 3. 广告位与触发时机（场景表 + 应用层编排类 + 触发日志）
## 4. 广告密度（指标表 + 评分 + 证据）
## 5. 用户控制与合规（设置项、权限、隐私）
## 6. 风险判断（应用行为风险 vs SDK 能力风险）
## 7. 待人工确认
## 附录 A SDK 能力与点击机制（非重点；标注"应用层接线/仅 SDK 内部"）
## 附录 B SDK 指纹（平台/版本/置信度）
## 附录 C 证据索引与复现命令
```

顺序原则：**应用行为在前，SDK 指纹与机制在后**。机制附录必须带「归因」列，
否则读者会把 SDK 能力误当成 App 行为。

PDF：`md2pdf.py` 自动加封面、目录（带页码）、页眉页脚；`--meta` 传包名/版本/样本哈希。

## 11. 判定速查

| 信号组合 | 结论 | 置信度 |
|---|---|---|
| 开屏 + 热启 + totalTimes≥3 | 密度高 | 强 |
| 分享/保存路径插屏 + 「点击微信」埋点 | 入口遮挡型误触 | 中强 |
| 穿透字段 + 频控日志 + 分发类 | 穿透点击已实现 | 强 |
| CNR 字段 + `cnr 第 N 次` 日志 | 关闭即点击 | 强 |
| `SHAKE_IT`/`twistValue` + 摇一摇文案 | 传感器点击 | 强 |
| 只有 `forceClickEnable` 字段 | 能力存在，未必启用 | 中 |
| 只有 `NonStandardAdSourceXxx` 枚举 | 配置层预留 | 弱 |

## 12. 常见误判

- **打包 ≠ 启用**：SDK 全量编译进来很常见；要看初始化与调用日志。
- **子串误报**：`isAutoDownload` 会命中 `MisAutoDownload...`；identifier 要加词边界（脚本已处理）。
- **平台自带 ≠ 媒体开启**：CSJ 包里有「摇一摇」素材，不代表媒体用了；要看设置页/AB/服务端字段。
- **加固/混淆**：类名被短名化时，只能靠 so、域名、日志文案。
- **Flutter 字符串压缩**：`libapp.so` 里搜不到中文是正常的，去 ArkTS 侧找。
- **枚举当成接入**：`NonStandardAdSourceBaidu` 只是服务端 protobuf 枚举。

## 13. 交付清单

- [ ] `scan.json` + `report.md`（自动初筛）
- [ ] 人工补全：平台表、类型表、密度评分、点击机制证据
- [ ] 合规表（已披露/未披露/无法判断）
- [ ] 证据索引（`strings/all.txt` 行号）
- [ ] Markdown + PDF（封面/目录/页码）
- [ ] 局限声明（无真机/无抓包时）

## 14. 案例类型（不含具体 App）

- **类型 A（误触型）**：摇一摇/扭一扭、开屏热启连播、分享入口遮挡、按点击人群卖量。
- **类型 B（能力依赖型）**：所选 SDK 提供穿透点击、CNR、强制/自动点击、SLD 传感器与互动物料。

> 具体 App 名称、包名与审计报告不在本仓库披露；写出报告时也应遵守授权范围。
