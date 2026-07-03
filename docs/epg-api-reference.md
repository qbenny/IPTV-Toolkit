# EPG 服务器接口技术文档

> **版本**：2.0 | **最后更新**：2026-07-03
>
> **API 所属**：浙江电信 IPTV — EPG 网关（华为 CTC 平台）+ VIS 节目单服务器
>
> **探针脚本**：`scratch/probe_epg_api.py`, `scratch/explore_epg_sources.py`, `scratch/test_schedules_cctv5.py`
>
> **2.0 更新**：新发现 VIS `api/schedules/` 接口 — 覆盖全部频道、无需认证、返回完整时间戳，是 EPG 模块的首选数据源。

---

## 目录

1. [概述](#1-概述)
2. [前置条件：认证](#2-前置条件认证)
3. [接口一览](#3-接口一览)
4. [★ 接口 1：VIS api/schedules/ — 全频道节目单（推荐）](#4-接口-1vis-apischedules)
5. [接口 2：data.jsp channelProgramList — 时移频道节目单](#5-接口-2datajsp-channelprogramlist)
6. [接口 3：data.jsp channelCurrentProg — 当前节目](#6-接口-3datajsp-channelcurrentprog)
7. [接口 4：data.jsp programInfo — 回看播放地址](#7-接口-4datajsp-programinfo)
8. [接口 5：data.jsp channelListAll — 频道编码映射表](#8-接口-5datajsp-channellistall)
9. [字段速查表](#9-字段速查表)
10. [代码参考](#10-代码参考)
11. [已知限制与注意事项](#11-已知限制与注意事项)

---

## 1. 概述

EPG 数据有两个独立来源：EPG 网关 `data.jsp` 和 VIS 节目单服务器 `api/schedules/`。

| 接口 | 服务器 | 覆盖范围 | 需要认证 | 推荐度 |
|------|--------|:---:|:---:|:---:|
| ★ `api/schedules/{code}.json` | VIS (58000端口) | **全部频道** | ❌ 不需要 | ⭐⭐⭐ |
| `data.jsp?Action=channelProgramList` | EPG 网关 | 仅 `isTvod=1` 频道 | ✅ 需要 | ⭐⭐ |
| `data.jsp?Action=channelCurrentProg` | EPG 网关 | 仅 `isTvod=1` 频道 | ✅ 需要 | ⭐ |

**推荐使用 VIS `api/schedules/` 作为 EPG 模块的主数据源**：覆盖全、无需认证、数据结构更规范。

---

## 2. 前置条件：认证

所有接口依赖机顶盒认证流程（`STBSimulator.login()`），需完成四步握手：

```
1. GET AuthenticationURL?Action=Login → 重定向获取 epg_base_url
2. POST authLoginHWCTC.jsp → 提取 EncryptToken + operator
3. DES-ECB 签名 → POST ValidAuthenticationHWCTC.jsp → 获取正式 UserToken
4. GET configUrl.min.js → 解析 VIS VOD 服务器地址
```

**认证实现**：`src/auth/simulator.py` — `STBSimulator.login()`

**请求特征**：
- 使用 `requests.Session` 保持 Cookie
- 公共请求头（`config.headers`）：
  ```python
  {
      "User-Agent": "Mozilla/5.0 (Linux; U; Android 4.0.3; zh-cn; EC6106V6U_pub_20_zjzdx...)",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
      "Accept-Encoding": "gzip, deflate",
      "Accept-Language": "zh; q=1.0, en; q=0.5",
      "Connection": "keep-alive"
  }
  ```

**认证后 Session 状态**：
- `sim.state.is_authenticated` = `True`
- `sim.state.epg_base_url` = EPG 网关地址（如 `http://218.71.130.66:33200`）
- `sim.state.session` — 携带认证 Cookie 的 Session 实例

---

## 3. 接口一览

### 3.1 VIS 节目单服务器（无需认证）

```
统一入口：http://115.233.200.60:58000/epg/api/schedules/{channelCode}.json

请求方式：GET（query string 传参）
编码：UTF-8，标准 JSON
认证：不需要（仅需标准 User-Agent 头）
```

### 3.2 EPG 网关（需要 STB 认证）

```
统一入口：{epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp

请求方式：GET（query string 传参）
编码：服务端响应为 GBK
解析工具：src/utils/helpers.py — parse_epg_json()
认证：必须先完成 STBSimulator.login()
```

### 3.3 频道编码映射（获取 channelCode）

```
Action=channelListAll → 返回所有频道的 channelID、code、isTvod、backTime
此映射表用于构造 VIS schedules API 的 URL
```

---

## 4. ★ 接口 1：VIS `api/schedules/`（推荐优先使用）

全频道节目单接口，覆盖所有频道（含非时移频道），无需认证。

### 4.1 请求参数

| 参数 | 类型 | 必填 | 说明 | 示例 |
|------|------|:---:|------|------|
| `begintime` | string | 否 | 起始日期，格式 `YYYYMMDD` | `20260703` |
| `endtime` | string | 否 | 结束日期，格式 `YYYYMMDD` | `20260704` |
| `date` | string | 否 | 单日查询 `YYYYMMDD`（与 range 互斥） | `20260703` |

不传任何参数时返回当天数据。

### 4.2 URL 格式

```
http://115.233.200.60:58000/epg/api/schedules/{channelCode}.json
    ?begintime=20260703
    &endtime=20260704
```

`channelCode` 来源：`data.jsp?Action=channelListAll` 返回的 `code` 字段。

### 4.3 请求示例

```python
import requests

# 无需 EPG 认证！
headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
url = f"http://115.233.200.60:58000/epg/api/schedules/00000001000000050000000000000600.json"
params = {"begintime": "20260703", "endtime": "20260704"}
res = requests.get(url, params=params, headers=headers, timeout=10)
data = res.json()
```

### 4.4 响应结构

```json
{
  "status": 200,
  "message": null,
  "pageInfo": null,
  "resultSet": [
    {
      "code": "3cc6562747894f1f833aff3a1b3ae12b",
      "channelCode": "00000001000000050000000000000427",
      "title": "山水间的家Ⅳ(1)",
      "showStartTime": "00:00",
      "isNow": 2,
      "startTime": "20260703000000",
      "endTime": "20260703001700"
    }
  ]
}
```

### 4.5 响应字段详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `title` | string | **节目名称** |
| `startTime` | string | **开始时间** `YYYYMMDDHHMMSS`（完整时间戳） |
| `endTime` | string | **结束时间** `YYYYMMDDHHMMSS` |
| `showStartTime` | string | 仅时间部分 `HH:MM` |
| `code` | string | 内容编码（32 位 hex） |
| `channelCode` | string | 频道编码（与请求 URL 中对应） |
| `isNow` | int | 是否当前节目（`1`=是，`2`=否） |

### 4.6 覆盖范围

| 频道类型 | data.jsp 有数据？ | VIS schedules 有数据？ |
|----------|:---:|:---:|
| timeshift=1 主力频道 | ✅ | ✅ |
| CCTV-3/5/6/8 等 | ❌ | ✅ |
| 卫视频道 | ✅ | ✅ |
| 地方台/购物台等 | 部分 | ✅ |
| CGTN、4K测试等 | ❌ | 待验证 |

> **`backTime` 字段**：`channelListAll` 返回的 `backTime` 指示该频道支持的回看天数（如 CCTV-1: 7天，CCTV-6: 3天）。

---

## 5. 接口 2：data.jsp `channelProgramList`

仅覆盖 `isTvod=1` 的频道，已不推荐作为主数据源。保留文档以备回退。

### 4.1 请求参数

| 参数 | 类型 | 必填 | 说明 | 示例 |
|------|------|:---:|------|------|
| `Action` | string | ✅ | 固定值 | `channelProgramList` |
| `channelId` | string | ✅ | 直播频道 ID（来自 `live_channels.channel_id`） | `4646` |
| `date` | string | ✅ | 查询日期，格式 `YYYYMMDD` | `20260703` |

### 4.2 请求示例

```
GET {epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp
    ?Action=channelProgramList
    &channelId=4646
    &date=20260703
```

Python 调用：
```python
data_url = f"{sim.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"
res = sim.state.session.get(
    data_url,
    params={
        "Action": "channelProgramList",
        "channelId": "4646",
        "date": "20260703"
    },
    headers=sim.config.headers,
    timeout=15
)
data = parse_epg_json(res.text)
```

### 4.3 响应结构

```json
{
  "result": [
    {
      "code": "3cc6562747894f1f833aff3a1b3ae12b",
      "day": "2026-07-03",
      "time": "00:00:00",
      "endtime": "00:17:00",
      "name": "山水间的家Ⅳ(1)",
      "proID": "149811578",
      "proflag": "1"
    }
  ]
}
```

### 4.4 响应字段详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | **节目名称**（GBK 编码，`parse_epg_json` 自动解码） |
| `day` | string | 节目日期，格式 `YYYY-MM-DD` |
| `time` | string | **开始时间**，格式 `HH:MM:SS`（24 小时制） |
| `endtime` | string | **结束时间**，格式 `HH:MM:SS` |
| `proID` | string | **节目唯一 ID**，用于 `Action=programInfo` 获取回放地址 |
| `proflag` | string | 回看标记，`"1"` = 支持回看，其他值 = 不支持 |
| `code` | string | 内容编码（UUID 风格，`[0-9a-f]{32}`） |

> **注意**：旧版 `run_simulator.py` 中使用的 `beginTime`/`endTime` 字段名是错误的，实际接口返回 `time`/`endtime`。

### 4.5 数据特征

| 指标 | 观测值 |
|------|--------|
| 单频道单日节目数 | 约 40~50 个（含深夜重播等） |
| 全频道全量单日 | 165 频道 × ~45 个 ≈ ~7,500 条/天 |
| 7 天全量估算 | ~52,500 条 |
| 历史数据可查范围 | 至少昨天可查（数据量显著减少，仅 5 条 vs 49 条） |

---

## 5. 接口 2：channelCurrentProg

批量获取多个频道"此刻正在播放"的节目名，适合实时 EPG 展示或快速确认频道是否在线。

### 5.1 请求参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `Action` | string | ✅ | `channelCurrentProg` |
| `channelIds` | string | ✅ | 频道 ID 列表，逗号分隔，**可传多个** |

### 5.2 请求示例

```
GET {epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp
    ?Action=channelCurrentProg
    &channelIds=4646,3844,5375944,5375950
```

### 5.3 响应结构

```json
{
  "result": [
    {
      "channelId": "4646",
      "time": "21:05:00",
      "endtime": "22:00:00",
      "name": "种墨园(14)"
    },
    {
      "channelId": "3844",
      "time": "20:31:00",
      "endtime": "21:31:00",
      "name": "奔跑吧"
    }
  ]
}
```

### 5.4 响应字段详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `channelId` | string | 频道 ID，对应请求中的 `channelIds` |
| `name` | string | 当前节目名称 |
| `time` | string | 当前节目开始时间，`HH:MM:SS` |
| `endtime` | string | 当前节目结束时间，`HH:MM:SS` |

> **注意**：该接口不返回 `proID`，无法直接用于获取回放地址。未在播的频道可能不出现在结果中（实测传 10 个 ID 返回 8 个）。

---

## 6. 接口 3：programInfo

查询单个节目的详细信息和 RTSP 回看播放地址。

### 6.1 请求参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `Action` | string | ✅ | `programInfo` |
| `channelId` | string | ✅ | 频道 ID |
| `programId` | string | ✅ | 节目 ID（来自 `channelProgramList` 返回的 `proID`） |

### 6.2 请求示例

```
GET {epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp
    ?Action=programInfo
    &channelId=4646
    &programId=149811578
```

### 6.3 响应结构

```json
{
  "result": {
    "channelCode": "00000001000000050000000000000427",
    "channelName": "中央一套高清",
    "code": "3cc6562747894f1f833aff3a1b3ae12b",
    "name": "山水间的家Ⅳ(1)",
    "time": "20260703000000",
    "endtime": "20260703001700",
    "introduce": "",
    "mediaUrl": "rtsp://218.71.128.109/TVOD/88888913/224/3221228078/...playseek=20260703000000-20260703001700..."
  }
}
```

### 6.4 响应字段详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 节目名称 |
| `channelName` | string | 频道名称（GBK 编码） |
| `channelCode` | string | 频道内部编码（不同于 `channel_id`） |
| `time` | string | 开始时间，格式 `YYYYMMDDHHMMSS` |
| `endtime` | string | 结束时间，格式 `YYYYMMDDHHMMSS` |
| `introduce` | string | **节目简介/描述**（可能为空字符串） |
| `code` | string | 内容编码，与 `channelProgramList` 中的 `code` 一致 |
| `mediaUrl` | string | **RTSP 回看播放地址**，含 `playseek` 时间范围和认证 token |

### 6.5 mediaUrl 解析

```
rtsp://218.71.128.109/TVOD/88888913/224/3221228078/10000100000000060000000002460690_0.smil
    ?rrsip=220.191.136.24,rrsip=220.191.137.183
    &playseek=20260703000000-20260703001700    ← 回看时间范围
    &zoneoffset=480                             ← UTC+8 时区偏移
    &recType=1                                  ← 录制类型
    &icpid=5568
    &limitflux=-1&limitdur=-1
    &tenantId=8601
    &accountinfo=...                            ← 认证信息（含 IP 和时间戳）
    &GuardEncType=2
    &it=...                                     ← 加密 token
```

---

## 7. 字段速查表

三种接口返回字段的交叉对照：

| 字段 | `channelProgramList` | `channelCurrentProg` | `programInfo` | 说明 |
|------|:---:|:---:|:---:|------|
| `time` | `HH:MM:SS` | `HH:MM:SS` | `YYYYMMDDHHMMSS` | 开始时间 |
| `endtime` | `HH:MM:SS` | `HH:MM:SS` | `YYYYMMDDHHMMSS` | 结束时间 |
| `day` | `YYYY-MM-DD` | — | — | 日期（仅节目单接口有） |
| `name` | ✅ | ✅ | ✅ | 节目名称 |
| `proID` | ✅ | — | 作为参数传入 | 节目唯一 ID |
| `proflag` | ✅ | — | — | `"1"`=可回看 |
| `code` | ✅ | — | ✅ | 内容编码（32 位 hex） |
| `channelId` | 作为参数传入 | ✅ | 作为参数传入 | 频道 ID |
| `channelName` | — | — | ✅ | 频道名（programInfo 特有） |
| `channelCode` | — | — | ✅ | 频道内部编码 |
| `introduce` | — | — | ✅ | 节目简介 |
| `mediaUrl` | — | — | ✅ | RTSP 回放地址 |

---

## 8. 接口 5：data.jsp `channelListAll`

获取所有频道的 `channelID` → `channelCode` 映射表，是使用 VIS schedules API 的前提。

### 8.1 请求参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `Action` | string | ✅ | `channelListAll` |

### 8.2 响应结构

```json
{
  "result": [
    {
      "channelID": 4646,
      "code": "00000001000000050000000000000427",
      "name": "中央一套高清",
      "isTvod": 1,
      "backTime": 7,
      "timeShift": 1,
      "viewChannelID": 1,
      "playChannelID": 1,
      "pic": "http://..."
    }
  ]
}
```

### 8.3 响应字段详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `channelID` | int | 频道 ID（对应 `live_channels.channel_id`） |
| `code` | string | **channelCode**，VIS schedules API 的 URL 参数 |
| `name` | string | 频道名称 |
| `isTvod` | int | `1`=支持时移回看，`0`=不支持 |
| `backTime` | int | **EPG 回看天数**（如 7=7天，3=3天，0=仅今天） |
| `timeShift` | int | `1`=有时移 |
| `pic` | string | 频道 Logo URL |

> **`backTime` 是同步天数决策的关键字段**：值为几就往前拉几天的 EPG。

---

## 9. 字段速查表

所有接口返回字段的交叉对照：

| 字段 | VIS schedules | data.jsp channelProgramList | data.jsp channelCurrentProg | data.jsp programInfo |
|------|:---:|:---:|:---:|:---:|
| 时间格式 | `YYYYMMDDHHMMSS` | `HH:MM:SS` | `HH:MM:SS` | `YYYYMMDDHHMMSS` |
| 节目名 | `title` | `name` | `name` | `name` |
| 开始时间 | `startTime` | `time` | `time` | `time` |
| 结束时间 | `endTime` | `endtime` | `endtime` | `endtime` |
| 内容编码 | `code` | `code` | — | `code` |
| 频道编码 | `channelCode` | — | — | `channelCode` |
| 当前节目标记 | `isNow` | — | — | — |
| `showStartTime` | `HH:MM` | — | — | — |
| `proID` | — | ✅ | — | 参数传入 |
| `proflag` | — | ✅ | — | — |
| `introduce` | — | — | — | ✅ |
| `mediaUrl` | — | — | — | ✅ |

### 9.1 VIS schedules vs data.jsp 差异

| 维度 | VIS `api/schedules/` | data.jsp `channelProgramList` |
|------|------|------|
| 认证要求 | ❌ 无需 | ✅ 需要 STB 登录 |
| 频道覆盖 | 全部频道（含 CCTV-5 等） | 仅 `isTvod=1` 频道 |
| 时间格式 | 完整 `YYYYMMDDHHMMSS` | 仅 `HH:MM:SS` |
| 日期信息 | `startTime` 自带日期 | 独立 `day` 字段 |
| JSON 格式 | 标准 JSON（`status:200` 包装） | 非标准（圆括号/单引号） |
| 请求方式 | 逐频道 `{code}.json` | 统一 `data.jsp` |

---

## 10. 代码参考

### 10.1 推荐实现（VIS schedules API）

```python
import requests

VIS_SCHEDULES = "http://115.233.200.60:58000/epg/api/schedules/"

def get_channel_codes(sim):
    """从 channelListAll 获取 channelID→code 映射"""
    data_url = f"{sim.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"
    res = sim.state.session.get(data_url, 
        params={"Action": "channelListAll"}, 
        headers=sim.config.headers, timeout=15)
    data = parse_epg_json(res.text)
    mapping = {}
    for item in data.get("result", []):
        mapping[str(item["channelID"])] = {
            "code": item["code"],
            "backTime": item.get("backTime", 0),
            "name": item.get("name", ""),
        }
    return mapping

def get_channel_schedule(channel_code, begintime, endtime):
    """拉取 VIS 节目单（无需认证）"""
    url = f"{VIS_SCHEDULES}{channel_code}.json"
    params = {"begintime": begintime, "endtime": endtime}
    res = requests.get(url, params=params,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"}, timeout=15)
    if res.status_code == 200:
        return res.json().get("resultSet", [])
    return []
```

### 10.2 现有代码位置

| 文件 | 内容 |
|------|------|
| `src/auth/simulator.py` | `login()` 认证流程、`get_channel_list()` 频道拉取 |
| `src/utils/helpers.py` | `parse_epg_json()` 非标准 JSON 解析 |
| `scratch/explore_epg_sources.py` | VIS schedules API 发现 + channelListAll 验证 |
| `scratch/test_schedules_cctv5.py` | 验证全频道覆盖（含 CCTV-5） |

---

## 11. 同步策略建议

基于两套接口的实测数据：

```
EPG 同步数据流：
┌─────────────────────────────────────────────────────────┐
│ 1. STB 登录 → channelListAll → 获取 channelCode 映射表  │
│ 2. 按 backTime 分档决定同步天数：                         │
│    backTime=7 → -7d ~ +1d                               │
│    backTime=3 → -3d ~ +1d                               │
│    backTime=0 → 今 ~ +1d                                │
│ 3. 逐频道查询 VIS schedules API（无需认证，纯 HTTP）     │
│ 4. UPSERT 写入 epg_programs                             │
│ 5. 生成 XMLTV XML                                       │
└─────────────────────────────────────────────────────────┘

优势：
  - channelListAll 只需调用 1 次（登录后）
  - 每个频道 1~2 次 schedules 请求（今+明 / 历史+今+明）
  - 无需 EPG session 保持（schedules API 裸请求即可）
  - 覆盖所有频道
```

---

## 更新记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-07-03 | 2.0 | 重大更新：发现 VIS `api/schedules/` 接口，覆盖全部频道且无需认证。新增 `channelListAll` 映射接口。推荐改用 VIS API 作为主数据源。 |
| 2026-07-03 | 1.0 | 初始版本，基于 `scratch/probe_epg_api.py` 实测 data.jsp 三个接口 |

### 8.2 旧版参考代码（需修正字段名）

`run_simulator.py` 中的实现使用了**错误的字段名**，迁移时需修正：

| 旧版（错误） | 实际接口（正确） |
|:---:|:---:|
| `item.get("beginTime")` | `item.get("time")` |
| `item.get("endTime")` | `item.get("endtime")` |

### 8.3 完整的 `get_tvod_program_list()` 推荐实现

```python
def get_tvod_program_list(self, channel_id: str, date_str: str = None) -> list:
    """拉取频道回看节目单。

    Args:
        channel_id: 频道 ID（来自 live_channels.channel_id）
        date_str: 日期 YYYYMMDD，默认为今天

    Returns:
        节目列表，每项含 name, time, endtime, day, proID, proflag, code
    """
    if not self.state.is_authenticated:
        self.logger.error("未认证，无法获取回看节目单。")
        return []

    if not date_str:
        date_str = time.strftime("%Y%m%d")

    data_url = f"{self.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"
    params = {
        "Action": "channelProgramList",
        "channelId": channel_id,
        "date": date_str
    }
    try:
        res = self.state.session.get(data_url, params=params, headers=self.config.headers, timeout=15)
        res_data = parse_epg_json(res.text)
        result = res_data.get("result", [])
        program_list = []
        for item in result:
            program_list.append({
                "program_id": str(item.get("proID", "")),
                "name": item.get("name", ""),
                "day": item.get("day", ""),
                "begin_time": item.get("time", ""),       # ← 注意：time 不是 beginTime
                "end_time": item.get("endtime", ""),      # ← 注意：endtime 不是 endTime
                "proflag": item.get("proflag", ""),
                "code": item.get("code", ""),
            })
        return program_list
    except Exception as e:
        self.logger.error("获取回看节目单发生异常: %s", e)
        return []
```

---

## 9. 已知限制与注意事项

### 接口层面

1. **内网限制**：EPG 网关地址（如 `218.71.130.66:33200`）是浙江省 IPTV 专网地址，外网无法访问
2. **认证依赖**：必须先完成 STB 四步认证，所有接口依赖同一个 `requests.Session` 的 Cookie
3. **历史数据有限**：`channelProgramList` 的历史数据保留范围不确定，实测昨天仅返回 5 条（今天 49 条）
4. **channelCurrentProg 不返回 proID**：需要回放地址时必须用 `channelProgramList` 获取完整节目信息
5. **无批量节目单接口**：`channelProgramList` 一次只能查一个频道，全量同步需逐频道循环请求

### 编码与解析

6. **服务端 GBK 编码**：原始响应为 GBK，`parse_epg_json()` 可正确解码
7. **非标准 JSON 格式**：响应可能被圆括号包裹 `(...)` 或使用单引号，`parse_epg_json()` 使用 `ast.literal_eval` 兼容处理
8. **`introduce` 字段经常为空**：`programInfo` 返回的节目简介多数为空字符串

### 性能与策略

9. **同步策略建议**：
   - 每次同步拉取全部启用频道 × 未来 7 天
   - 单频道请求约 100~300ms，165 个频道 × 7 天 ≈ 1155 次请求
   - 建议添加请求间隔（如 200ms），避免对服务器造成压力
   - 可优先拉取支持时移（`timeshift_enabled=1`）的频道
10. **去重策略**：按 `(channel_id, date, begin_time, name)` 判断重复，`proID` 可能在不同日期重复出现

---

## 附录：相关 JSP 页面

从 `configUrl.min.js` 提取的 EPG/回看相关路由：

| 变量 | 值 | 说明 |
|------|-----|------|
| `column_url.program` | `pageChannelTvod.jsp` | 回看节目列表页 |
| `column_url.live` | `pageChannelList.jsp` | 直播频道列表页 |
| `play_url.back` | `media_play_all.jsp?mediaType=tvod` | 回看播放页面 |

---

## 更新记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-07-03 | 1.0 | 初始版本，基于 `scratch/probe_epg_api.py` 实测数据 |
