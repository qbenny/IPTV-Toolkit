# EPG 模块技术文档

> 版本: 1.0 | 最后更新: 2026-07-03
>
> 本文档描述 EPG 模块的完整架构、数据流、接口和运维指南。

---

## 目录

1. [概述](#1-概述)
2. [架构](#2-架构)
3. [数据来源](#3-数据来源)
4. [数据库设计](#4-数据库设计)
5. [同步模块](#5-同步模块)
6. [API 接口](#6-api-接口)
7. [XMLTV 生成](#7-xmltv-生成)
8. [Web UI](#8-web-ui)
9. [与直播模块的集成](#9-与直播模块的集成)
10. [运维指南](#10-运维指南)
11. [已知限制](#11-已知限制)
12. [附录](#12-附录)

---

## 1. 概述

EPG 模块从浙江电信 IPTV VIS 节目单服务器拉取全频道 EPG 数据，存入本地 SQLite 数据库，并生成 XMLTV 格式的 EPG XML 文件供 IPTV 客户端使用。

### 1.1 模块文件

| 文件 | 作用 |
|------|------|
| `src/sync/epg_sync.py` | 同步核心：拉取、去重、写入、清理 |
| `src/api/epg.py` | API 路由：同步触发、XML 生成、节目查询 |
| `src/db/models.py` | `epg_programs` 表定义 |
| `main.py` | 路由注册 + 依赖注入 |

### 1.2 配套探针脚本

| 文件 | 作用 |
|------|------|
| `scratch/probe_epg_api.py` | data.jsp 接口验证 |
| `scratch/explore_epg_sources.py` | VIS API 发现 |
| `scratch/test_schedules_cctv5.py` | 全频道覆盖验证 |
| `scratch/probe_epg_range.py` | 日期边界探测 |
| `scratch/probe_epg_timeshift_boundary.py` | 时移长度 vs EPG 边界 |
| `scratch/probe_epg_notimeshift_all.py` | 非时移频道 EPG 扫描 |
| `scratch/check_epg_range.py` | 数据库日期范围检查 |
| `scratch/check_4k_epg_match.py` | 4K/HD EPG 匹配验证 |

---

## 2. 架构

```
┌──────────────────────────────────────────────────────────────┐
│                      IPTV 服务器                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  EPG 网关 (data.jsp)                                     │ │
│  │  └─ Action=channelListAll → channelCode + backTime       │ │
│  └─────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  VIS 节目单服务器 (58000 端口)                            │ │
│  │  └─ api/schedules/{code}.json → 标准 JSON 节目单         │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
                    │                    │
                    │ STB 认证            │ 无需认证
                    │ (1 次请求)          │ (85 次请求)
                    ▼                    ▼
┌──────────────────────────────────────────────────────────────┐
│                    EPG 同步模块 (epg_sync.py)                  │
│                                                                │
│  channelListAll  ──→  channelCode映射 + backTime              │
│        │                                                      │
│        ▼                                                      │
│  HD/SD 去重 (同 code 只保留一个)                               │
│        │                                                      │
│        ▼                                                      │
│  按 backTime 分档:                                            │
│    backTime≥7 → begin=-7d, end=+1d  (9天)                    │
│    backTime≥3 → begin=-3d, end=+1d  (5天)                    │
│    backTime<3 → begin=今天, end=+1d  (2天)                    │
│        │                                                      │
│        ▼                                                      │
│  VIS api/schedules/ → 节目数据                                 │
│        │                                                      │
│        ▼                                                      │
│  UPSERT 写入 epg_programs                                     │
│        │                                                      │
│        ▼                                                      │
│  清理 9 天前的过期数据                                         │
└──────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────────────┐
│                  epg_programs 表 (SQLite)                      │
│                                                                │
│  UNIQUE(channel_id, start_time, title)                        │
│  索引: channel_id / program_date / epg_channel_id             │
└──────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────────────┐
│  API 输出                                                      │
│  ┌──────────────────────────────────────────────────────────┐│
│  │ GET /epg.xml            → XMLTV EPG XML                  ││
│  │ GET /api/epg/programs   → 节目查询                        ││
│  │ GET /api/epg/programs/now → 当前播放                       ││
│  │ GET /api/epg/stats      → 统计                            ││
│  └──────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────┘
```

---

## 3. 数据来源

### 3.1 主数据源：VIS schedules API（无需认证）

```
GET http://115.233.200.60:58000/epg/api/schedules/{channelCode}.json
    ?begintime=YYYYMMDD
    &endtime=YYYYMMDD
```

**特性**：
- 无需 STB 认证，标准 HTTP GET
- 覆盖全部频道（含 CCTV-3/5/6/8 等非时移频道）
- 返回标准 JSON，字段为 `title`/`startTime`/`endTime`
- 时间格式为 `YYYYMMDDHHMMSS`（完整时间戳，避免日期歧义）
- 一次请求可拉取 9 天数据（已验证范围不会截断）

### 3.2 channelCode 映射源：data.jsp channelListAll（需要认证）

```
GET {epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp
    ?Action=channelListAll
```

**返回关键字段**：

| 字段 | 说明 |
|------|------|
| `channelID` | 频道数字 ID |
| `code` | channelCode，VIS API 的 URL 参数 |
| `name` | 频道名 |
| `backTime` | EPG 可用回看天数（7/3/0），决定同步日期范围 |
| `isTvod` | 1=支持时移，0=不支持 |

> **注意**：`backTime` 并非总是准确，VIS API 实际返回的数据范围可能比 backTime 更广或更窄。当前以 backTime 为参考，配合 VIS API 实际返回结果为准。

### 3.3 频道数据覆盖（实测 2026-07-03）

| 类别 | 频道数 | 天数 | 典型频道 |
|------|:---:|:---:|------|
| backTime=7 | 37 | 9天 | CCTV1/2/5/7/12, 全部卫视频道高清, 地方台 |
| backTime=3 | 41 | 5天 | CCTV3/4/6/8/新闻/少儿/戏曲/音乐, CGTN, 购物 |
| 无 EPG | 48 | — | 广播电台、4K 频道、测试频道、凤凰系列、部分卫视标清 |

---

## 4. 数据库设计

### 4.1 epg_programs 表

```sql
CREATE TABLE IF NOT EXISTS epg_programs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id      TEXT NOT NULL,             -- 服务器原始 channelID（关联 live_channels）
    channel_name    TEXT DEFAULT '',            -- 服务器频道名
    title           TEXT DEFAULT '',            -- 节目名称
    start_time      TEXT NOT NULL,              -- 开始时间 "YYYY-MM-DD HH:MM:SS"
    end_time        TEXT NOT NULL,              -- 结束时间 "YYYY-MM-DD HH:MM:SS"
    program_date    TEXT NOT NULL,              -- 日期 "YYYY-MM-DD"（提取自 start_time）
    epg_channel_id  TEXT DEFAULT '',            -- EPG 归一化 ID（如 "CCTV1", "浙江卫视"）
    raw_data_json   TEXT DEFAULT '',            -- 原始 JSON 备份
    synced_at       INTEGER DEFAULT 0,          -- 同步时间戳
    created_at      INTEGER DEFAULT 0           -- 创建时间戳
);
```

### 4.2 索引

| 索引 | 用途 |
|------|------|
| `UNIQUE (channel_id, start_time, title)` | 去重约束 |
| `idx_epg_channel` | 按频道查询 |
| `idx_epg_date` | 按日期查询 / 过期清理 |
| `idx_epg_epg_ch` | XML 生成时按归一化 ID 分组 |

### 4.3 配置项

在 `live_config` 表中：

| key | 默认值 | 说明 |
|-----|--------|------|
| `epg_auto_sync` | `1` | 是否启用自动同步（预留） |

---

## 5. 同步模块 (`src/sync/epg_sync.py`)

### 5.1 核心常量

```python
VIS_SCHEDULES_BASE  = "http://115.233.200.60:58000/epg/api/schedules/"
_REQUEST_INTERVAL   = 0.2   # 请求间隔（秒）
_MAX_RETRIES        = 3     # 单频道最大重试次数
_RETRY_DELAY_BASE   = 3     # 指数退避基数（秒）
_RETRY_BATCH_INTERVAL = 2  # 第二轮重试间隔（秒）
_MAX_BATCH_RETRIES  = 2    # 第二轮整体重试次数
```

### 5.2 同步流程 (`full_sync`)

```
1. STB 登录（通过外部注入的 sim）
2. channelListAll → 获取 161 个频道的 channelCode + backTime
3. 按 channelCode 去重（HD/SD 同 code 只取一个）
4. 逐频道查询 VIS schedules API：
   ├─ 请求成功且有数据 → UPSERT 写入
   ├─ 请求成功但无数据 → 记为"无EPG"（不重试）
   └─ 请求失败（超时等） → 加入重试队列
5. 对失败队列做 2 轮整体重试
6. 清理 9 天前的过期数据
```

### 5.3 频道名归一化 (`_normalize_epg`)

```
"浙江卫视高清" → "浙江卫视"
"CCTV1+"       → "CCTV1+"
"中央一套高清" → "中央一套"
"中央奥运4K"   → "中央奥运"
```

去掉画质后缀（高清/标清/4K/8K/HD/SD 等），CCTV 频道提取台号。

### 5.4 重试机制

**单频道重试**（`_MAX_RETRIES = 3`）：
- 指数退避：3s → 6s → ... 
- 仅在 `requests.RequestException` 时重试
- HTTP 非 200 不重试

**批量重试**（`_MAX_BATCH_RETRIES = 2`）：
- 主循环结束后，对第一轮网络失败的频道做 2 轮整体重试
- 每轮重试间隔 2 秒
- 重试成功立即写入数据库

### 5.5 同步状态

```python
epg_sync_status = {
    "running": False,
    "progress": "",
    "current_channel": "",
    "done": 0,
    "total": 0,
    "last_sync_time": None,
    "last_error": None,
    "channel_count": 0,
    "program_count": 0,
}
```

通过 `POST /api/epg/sync` 触发，`GET /api/epg/sync/status` 轮询。

### 5.6 性能（实测）

| 场景 | 请求数 | 数据量 | 耗时 |
|------|:---:|:---:|:---:|
| 初次全量同步 | ~85 次 | ~30,000 条 | ~40 秒 |
| 日常增量同步 | ~85 次 | ~7,000 条 | ~35 秒 |

---

## 6. API 接口

Base URL: `/api/epg`

### 6.1 同步管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/epg/sync` | 触发后台同步（需 STB 认证） |
| `GET` | `/api/epg/sync/status` | 获取同步进度 |

### 6.2 节目查询

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| `GET` | `/api/epg/programs` | `channel_id`, `date`, `keyword`, `page`, `limit` | 分页查询节目单 |
| `GET` | `/api/epg/programs/now` | — | 当前正在播的节目 |
| `GET` | `/api/epg/stats` | — | EPG 统计信息 |

### 6.3 XML 输出

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/epg/xmltv.xml` | XMLTV 格式 EPG |
| `GET ` | `/epg.xml` | 快捷路由（挂在根路径） |

### 6.4 响应示例

**`POST /api/epg/sync`**：
```json
{"status": "started", "message": "EPG 同步已启动"}
```

**`GET /api/epg/sync/status`**：
```json
{
  "running": false,
  "progress": "同步完成",
  "last_sync_time": "2026-07-03T23:40:00",
  "channel_count": 78,
  "program_count": 29779
}
```

**`GET /api/epg/stats`**：
```json
{
  "total_programs": 29779,
  "total_channels": 78,
  "date_range": {"earliest": "2026-06-26", "latest": "2026-07-04"}
}
```

---

## 7. XMLTV 生成

### 7.1 格式

标准 XMLTV DTD，UTF-8 编码，`generator-info-name="IPTV-Toolkit EPG"`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE tv SYSTEM "xmltv.dtd">
<tv generator-info-name="IPTV-Toolkit EPG">
  <channel id="CCTV1">
    <display-name>中央一套高清</display-name>
  </channel>
  <programme channel="CCTV1" start="20260703000000 +0800" stop="20260703002000 +0800">
    <title lang="zh">新闻联播</title>
  </programme>
</tv>
```

### 7.2 筛选规则

- 频道：所有 `epg_channel_id != ''` 的记录
- 节目：仅输出 `end_time >= 今天 00:00:00`（已播完的不输出，减小 XML 体积）

### 7.3 与 M3U 的关联

- XML 的 `<channel id>` = 频道名归一化结果 (`epg_channel_id`)
- M3U 的 `tvg-id` = `live_channels.tvg_id`（同样经归一化）
- 两者通过归一化 ID 自动匹配，无需额外配置

**4K/HD/SD 共享 EPG**：
```
"浙江卫视4K" → tvg_id="浙江卫视"
"浙江卫视高清" → tvg_id="浙江卫视"  ← EPG 数据在这里
"浙江卫视" → tvg_id="浙江卫视"

全部指向 XML 中的 <channel id="浙江卫视">
```

---

## 8. Web UI

### 8.1 Tab 位置

导航栏第 3 项 "📅 EPG 节目管理"，位于直播频道管理和数据同步管理之间。

### 8.2 功能面板

| 面板 | 内容 |
|------|------|
| EPG 节目同步 | 同步状态 / 进度 / 上次同步时间 / 触发按钮 / EPG XML 地址复制 |
| EPG 数据统计 | 节目总数 / 频道数 / 日期范围 |
| 当前正在播放 | 各频道当前节目 + 时间段 / 刷新按钮 |

### 8.3 数据刷新

- EPG Tab 激活时自动拉取 `stats` 和 `sync/status`
- 同步进行中时每 2 秒轮询状态，完成后自动刷新统计
- 当前节目需手动点击"刷新"

---

## 9. 与直播模块的集成

### 9.1 main.py 注册

```python
# 依赖注入
from src.api.epg import set_simulator as set_sim_epg, set_login_func as set_login_epg
set_sim_epg(sim)
set_login_epg(login_sim)

# 路由注册
from src.api.epg import router as epg_router
app.include_router(epg_router)

# 快捷路由
from src.api.epg import get_xmltv
app.get("/epg.xml")(get_xmltv)
```

### 9.2 M3U 头部引用

在 `live_config` 中设置 `epg_url` 指向本服务：

```
epg_url = "http://{server_ip}:8880/epg.xml"
```

M3U 头部将自动生成：
```m3u
#EXTM3U x-tvg-url="http://192.168.1.100:8880/epg.xml"
```

### 9.3 数据库共享

EPG 和直播模块共享同一 SQLite 数据库 (`data/iptv.db`)：
- `live_channels` 表提供 tvg_id
- `epg_programs` 表提供节目数据
- 通过 `epg_channel_id` = `tvg_id` 关联

---

## 10. 运维指南

### 10.1 首次部署

```bash
# 1. 启动服务（自动 init_db 创建 epg_programs 表）
python main.py

# 2. 配置 STB 凭证并登录（Web UI → 系统凭证配置）

# 3. 触发 EPG 同步（Web UI → EPG 节目管理 → 开始 EPG 同步）
#    或: curl -X POST http://localhost:8880/api/epg/sync
```

### 10.2 日常维护

- **每日自动同步**：建议配置 cron/定时任务每日凌晨触发 `POST /api/epg/sync`
- **手动同步**：EPG Tab 点击"开始 EPG 同步"
- **数据清理**：自动保留 9 天，无需手动清理

### 10.3 日志关键字

```
[EPG Sync] 获取到 %d 个频道的编码映射      → Step 1 成功
[EPG Sync] 去重后 %d 个唯一频道待同步        → Step 2 成功
[EPG Sync] 第一轮: %d 有数据, %d 无EPG     → 主循环结果
[EPG Sync] 请求失败 (尝试 %d/%d)           → 网络问题
[EPG Sync] 第 2 轮重试 %d 个失败频道        → 批量重试
[EPG Sync] 完成: %d 频道, %d 条            → 最终结果
[EPG Sync] 清理 %d 条超过 %d 天的过期节目    → 数据清理
```

### 10.4 常见问题

| 问题 | 原因 | 处理 |
|------|------|------|
| 48 个频道"失败" | 广播/4K/测试频道无 EPG | 正常，日志会区分"无EPG"和"网络失败" |
| 同步后没有历史数据 | `_clean_expired` 阈值问题 | 检查 `keep_days` 参数 |
| VIS API 超时 | 网络波动 | 3 次重试 + 2 轮批量重试可恢复 |
| 频道数少于预期 | channelListAll 返回不完整 | 重新登录 STB 后重试 |

### 10.5 数据库查询示例

```sql
-- 统计各频道数据范围
SELECT epg_channel_id, MIN(program_date), MAX(program_date), COUNT(*)
FROM epg_programs GROUP BY epg_channel_id;

-- 查询某频道节目
SELECT * FROM epg_programs 
WHERE epg_channel_id = 'CCTV1' 
ORDER BY start_time;

-- 当前播放
SELECT * FROM epg_programs
WHERE start_time <= datetime('now','localtime')
  AND end_time >= datetime('now','localtime');
```

---

## 11. 已知限制

| 限制 | 说明 | 影响 |
|------|------|------|
| VIS API 依赖固定 IP | `115.233.200.60:58000` 硬编码 | 换网需修改 |
| channelCode 映射依赖 channelListAll | 需 STB 认证 | 不影响定时同步（认证由心跳维护） |
| 广播/4K/测试频道无 EPG | 共 48 个频道 | 不影响核心卫视/CCTV |
| backTime 不完全准确 | `backTime=3` 但实际最多 2 天历史 | 以实际返回为准 |
| 无单频道增量更新 | 每次全量同步 85+ 频道 | 耗时 ~40 秒，可接受 |
| 无节目描述/简介 | VIS API 未返回 description 字段 | M3U 播放器不展示节目详情 |
| XML 无 logo URL | 频道声明不含 `<icon>` | M3U 中有 logo，XML 中可手动补 |

---

## 12. 附录

### A. VIS schedules API 响应格式

```json
{
  "status": 200,
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

### B. 探针测试结论汇总

| 测试 | 结论 |
|------|------|
| 9 天范围一次请求 | ✅ 375 条节目正常返回 |
| CCTV-5 HD 有数据 | ✅ VIS API 覆盖非时移频道 |
| 4K 全部无数据 | ✅ 9 个 4K 频道 schedules API 返回 0 |
| HD=SD 节目一致 | ✅ 时间 + 名称 100% 一致 |
| 广播无 EPG | ✅ 7 个广播电台返回 0 |
| 凤凰/CGTN 外语 | ✅ 返回 0（合法无数据） |

### C. 实施历史

| 日期 | 内容 |
|------|------|
| 2026-07-03 | 初始实现：VIS API 同步 + XMLTV 生成 + Web UI |
| 2026-07-03 | 修复：增加重试机制、区分"无数据"和"网络失败" |
| 2026-07-03 | 修复：`_clean_expired` 从当天改为 9 天阈值 |
| 2026-07-03 | 确认：78 频道 29,779 条、37 频道 9 天、41 频道 5 天 |
