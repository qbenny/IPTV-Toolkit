# IPTV EPG 电子节目单模块 — 需求规格说明书

> 版本: 1.2 | 最后更新: 2026-07-03
>
> 服务器 EPG 接口参考：`docs/epg-api-reference.md`
>
> **1.2 更新**：发现 VIS `api/schedules/` 接口 — 覆盖全部频道（含CCTV-5/3/6/8等）、无需认证、返回完整时间戳。同步方案从 data.jsp 切换为 VIS API 为主。
>
> **1.1 更新**：基于 7 轮探针实测，补充时移长度-EPG边界关联、非时移频道 EPG 发现、清晰度合并策略等。

---

## 一、模块概述

本模块实现 IPTV EPG（Electronic Program Guide，电子节目单）的完整生命周期管理，包括：

1. **EPG 数据拉取**：从服务器按频道逐日拉取完整节目时间表，存入本地数据库
2. **EPG XML 生成**：生成符合 XMLTV 标准的 EPG XML 文件，供 TiviMate / Kodi / Perfect Player 等 IPTV 客户端使用
3. **同步调度**：支持手动触发 + 定时自动同步，后台线程执行，不阻塞 Web API
4. **Web UI**：同步状态查看、节目单浏览、EPG XML 订阅地址展示
5. **与直播模块集成**：M3U 中的 `x-tvg-url` 指向本模块生成的 EPG XML 地址

---

## 二、数据来源

### 2.1 主数据源：VIS `api/schedules/`（推荐）

> 详见 `docs/epg-api-reference.md` §4

**优势**：覆盖全部频道、无需 EPG 认证、标准 JSON、完整时间戳。

```
GET http://115.233.200.60:58000/epg/api/schedules/{channelCode}.json
    ?begintime=20260703
    &endtime=20260704
```

**返回**：`title`（节目名）、`startTime`（开始 `YYYYMMDDHHMMSS`）、`endTime`（结束）、`channelCode`、`isNow`

**前置步骤**：从 `data.jsp?Action=channelListAll` 获取 `channelID` → `channelCode` 映射表 + `backTime`（回看天数）。

### 2.2 辅助接口（不再作为主数据源）

| 接口 | 说明 |
|------|------|
| `data.jsp?Action=channelProgramList` | 仅覆盖 `isTvod=1` 频道，保留作为回退方案 |
| `data.jsp?Action=channelCurrentProg` | 实时校验当前节目 |
| `data.jsp?Action=programInfo` | 获取回看播放地址（需 proID） |

---

## 三、数据库设计

### 3.1 节目单表 `epg_programs`

```sql
CREATE TABLE IF NOT EXISTS epg_programs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id      TEXT NOT NULL,             -- FK → live_channels.channel_id
    channel_name    TEXT DEFAULT '',            -- 频道原始名称（冗余，便于查询）
    title           TEXT DEFAULT '',            -- 节目名称
    description     TEXT DEFAULT '',            -- 节目描述/简介（来自 programInfo）
    start_time      TEXT NOT NULL,              -- 开始时间 "YYYY-MM-DD HH:MM:SS"（本地时间）
    end_time        TEXT NOT NULL,              -- 结束时间 "YYYY-MM-DD HH:MM:SS"
    program_date    TEXT NOT NULL,              -- 节目日期 "YYYY-MM-DD"（用于批量清理）
    program_id      TEXT DEFAULT '',            -- 服务器 proID
    proflag         TEXT DEFAULT '',            -- 回看标记，"1"=支持
    content_code    TEXT DEFAULT '',            -- 服务器 code（32位hex）
    epg_channel_id  TEXT DEFAULT '',            -- XMLTV 中的 channel id（= live_channels.tvg_id）
    raw_data_json   TEXT DEFAULT '',            -- 服务器返回的原始 JSON 全量备份
    synced_at       INTEGER DEFAULT 0,          -- 同步时间戳
    created_at      INTEGER DEFAULT 0
);

-- 去重约束：同一频道、同一开始时间、同一节目名 = 重复
CREATE UNIQUE INDEX IF NOT EXISTS idx_epg_dedup
    ON epg_programs(channel_id, start_time, title);

-- 查询索引
CREATE INDEX IF NOT EXISTS idx_epg_channel   ON epg_programs(channel_id);
CREATE INDEX IF NOT EXISTS idx_epg_date      ON epg_programs(program_date);
CREATE INDEX IF NOT EXISTS idx_epg_time      ON epg_programs(start_time, end_time);
CREATE INDEX IF NOT EXISTS idx_epg_epg_ch_id ON epg_programs(epg_channel_id);
```

### 3.2 设计要点

| 决策 | 原因 |
|------|------|
| `start_time` / `end_time` 存为可读文本 | SQLite 无原生 DATETIME 类型；字符串格式 `YYYY-MM-DD HH:MM:SS` 可排序、可比较 |
| `channel_name` 冗余 | EPG 同步和 XML 生成时避免反复 JOIN `live_channels` 表 |
| `epg_channel_id` 预计算 | XML 生成时的 `<channel id>` 值，同步时从 `live_channels.tvg_id` 复制 |
| `raw_data_json` 全量备份 | 与直播模块 `live_channels.raw_fields_json` 一致，方便排错和回填 |
| 去重键 `(channel_id, start_time, title)` | 同一频道同时开始且同名的节目必定重复 |
| `program_date` 独立列 | 比从 `start_time` 截取更高效，用于按天批量清理过期数据 |

### 3.3 配置项新增

在 `live_config` 表中新增以下配置项：

| key | 说明 | 默认值 |
|-----|------|--------|
| `epg_auto_sync` | 是否启用每日自动同步 | `1` |
| `epg_include_description` | 同步时是否拉取每个节目的简介（会增加 N 倍请求） | `0` |

> **注意**：不再需要 `epg_sync_days` 配置。同步天数由频道的 `timeshift_length` 和是否有 EPG 数据自动决定，见附录 C。

---

## 四、同步模块 `src/sync/epg_sync.py`

### 4.1 同步流程

```
┌─────────────────────────────────────────────────────────────────┐
│ EPG 全量同步                                                     │
├─────────────────────────────────────────────────────────────────┤
│ 1. STB 登录                                                      │
│ 2. channelListAll → 获取 channelID→channelCode 映射 + backTime   │
│ 3. 按 backTime 分档：                                            │
│    backTime=7 → -7d ~ +1d                                       │
│    backTime=3 → -3d ~ +1d                                       │
│    backTime<3 → 今 ~ +1d                                        │
│ 4. 清晰度合并：同频道 HD/SD 共享同一 code                        │
│ 5. 逐频道查询 VIS schedules API（无需认证，纯 HTTP GET）         │
│    ├─ 每个请求间隔 200ms                                         │
│    ├─ 解析标准 JSON resultSet                                    │
│    └─ UPSERT 写入 epg_programs                                   │
│ 6. 清理过期节目：DELETE WHERE end_time < 今天 00:00:00            │
│ 7. 刷新 XMLTV 缓存                                               │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 同步策略

| 维度 | 方案 |
|------|------|
| **数据源** | VIS `api/schedules/`（主要）+ data.jsp（回退） |
| **触发方式** | 手动 `POST /api/epg/sync` + 定时（每日凌晨自动） |
| **认证需求** | 仅 channelListAll 需要 STB 登录；schedules 接口无需认证 |
| **同步天数** | 按 `backTime` 自动决定（7/3/0 分档） |
| **清晰度合并** | 同频道 HD/SD 共享 channelCode，只同步一次 |
| **过期清理** | 同步完成后删除 `end_time < 今天 00:00` 的记录 |
| **失败处理** | 单频道失败不中断全局 |

### 4.3 性能估算

| 指标 | 估算值 |
|------|--------|
| 需同步的唯一频道数 | ~85 个 |
| channelListAll 请求 | **1 次**（登录后一次性） |
| 初首次 schedules 请求 | ~170 次（85频道 × ~2天平均） |
| 日常 schedules 请求 | ~85 次（每频道拉今+明） |
| **初首次总耗时** | **~1 分钟** |
| **日常总耗时** | **~30 秒** |

### 4.4 同步状态结构

```python
epg_sync_status = {
    "running": False,
    "progress": "",           # 当前进度描述
    "current_channel": "",    # 当前处理的频道名
    "done": 0,
    "total": 0,
    "last_sync_time": None,   # 上次完成时间戳（ISO 8601）
    "last_error": None,       # 最近错误信息
    "channel_count": 0,       # 成功同步的频道数
    "program_count": 0,       # 写入的节目总数
}
```

---

## 五、EPG XML 生成

### 5.1 XMLTV 格式

生成符合 [XMLTV DTD](https://xmltv.org/xmltv.dtd) 标准的 XML 文件。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE tv SYSTEM "xmltv.dtd">
<tv generator-info-name="IPTV-Toolkit EPG" generator-info-url="http://192.168.1.3:8880">
  <!-- 频道定义 -->
  <channel id="CCTV1">
    <display-name>CCTV-1 综合</display-name>
    <display-name>CCTV1</display-name>
    <icon src="http://192.168.1.3:8880/static/logo/CCTV1.png"/>
  </channel>
  <channel id="浙江卫视">
    <display-name>浙江卫视</display-name>
    <icon src="http://192.168.1.3:8880/static/logo/浙江卫视.png"/>
  </channel>

  <!-- 节目数据 -->
  <programme channel="CCTV1" start="20260703070000 +0800" stop="20260703073000 +0800">
    <title lang="zh">朝闻天下</title>
    <desc lang="zh">国内外重大新闻...</desc>
    <category lang="zh">新闻</category>
  </programme>
  <programme channel="浙江卫视" start="20260703203100 +0800" stop="20260703213100 +0800">
    <title lang="zh">奔跑吧</title>
  </programme>
</tv>
```

### 5.2 关键映射

| XMLTV 元素 | 数据来源 | 说明 |
|------------|----------|------|
| `<channel id>` | `live_channels.tvg_id` | 与 M3U 中 `tvg-id` 一致 |
| `<display-name>` | `live_channels.name` | 频道显示名 |
| `<icon src>` | `live_config.logo_base_url` + `live_channels.logo_url` | 与 M3U 中 `tvg-logo` 一致 |
| `<programme channel>` | 同上 `tvg_id` | 关联频道 |
| `<programme start>` | `epg_programs.start_time` 转 `YYYYMMDDHHMMSS +0800` | |
| `<programme stop>` | `epg_programs.end_time` 同上 | |
| `<title>` | `epg_programs.title` | |
| `<desc>` | `epg_programs.description` | 可选，多数为空 |

### 5.3 生成策略

| 维度 | 方案 |
|------|------|
| **数据范围** | 从今天开始、`end_time >= now` 的所有节目 |
| **包含频道** | 仅输出在 `epg_programs` 中有数据的频道（无节目表的频道不输出空 `<channel>`） |
| **缓存** | 内存缓存，同步完成后刷新；避免每次请求都查库组装 |
| **GZip 压缩** | 可选 `.xml.gz` 输出，减少网络传输（IPTV 客户端通常支持） |

### 5.4 频道 ID 的 EPG 匹配

利用已有的 `normalize_epg()` 函数（`src/utils/normalize.py`）：

```
live_channels.name: "CCTV1综合高清"
    → normalize_epg() → "CCTV1"  → 用作 XMLTV <channel id>
    → M3U tvg-id="CCTV1" → IPTV 客户端据此匹配 EPG 数据
```

---

## 六、代码组织结构

### 6.1 新增文件

| 文件 | 角色 |
|------|------|
| `src/api/epg.py` | **核心**：EPG API 路由（同步触发、XML 生成、节目查询） |
| `src/sync/epg_sync.py` | EPG 后台同步逻辑（参考 `filter_sync.py` 的架构） |

### 6.2 需修改的文件

| 文件 | 改动 |
|------|------|
| `src/db/models.py` | `init_db()` 中新增 `epg_programs` 建表语句 + 索引 + 配置项 |
| `src/auth/simulator.py` | 新增 `get_tvod_program_list()` 方法（从旧 `run_simulator.py` 迁移，修正字段名） |
| `main.py` | `app.include_router(epg.router)` 注册路由；注入 `sim` 和 `login_func` 依赖 |

### 6.3 架构复用

| 已有基础设施 | EPG 模块如何复用 |
|-------------|-----------------|
| `STBSimulator` 认证 + Session | EPG 同步直接使用 `sim.state.session` 请求 `data.jsp` |
| `parse_epg_json()` | 解析三个 EPG 接口的非标准 JSON 响应 |
| `normalize_epg()` | 频道名归一化，用于 XMLTV `<channel id>` |
| `live_channel_aliases` + `resolve_channel_names()` | 别名映射，确保 EPG 台名与 M3U `tvg-id` 一致 |
| `filter_sync.py` 的同步框架 | 后台线程 + 状态暴露 + 指数退避重试，直接复用模式 |
| `live.py` 的代码风格 | Router prefix、`get_db_connection()`、`get_live_configs()` |

---

## 七、API 设计

Router: `prefix="/api/epg"`, `tags=["epg"]`

### 7.1 同步管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/epg/sync` | 触发 EPG 数据同步（后台线程执行） |
| `GET` | `/api/epg/sync/status` | 查询同步状态与进度 |

**`POST /api/epg/sync` 请求**：无 Body。

**响应**：
```json
{
  "status": "started",
  "message": "EPG 同步已启动，预计同步 85 个频道"
}
```

**`GET /api/epg/sync/status` 响应**：
```json
{
  "running": true,
  "progress": "正在同步 中央一套高清 (5/150) 第 3 天",
  "current_channel": "中央一套高清",
  "done": 15,
  "total": 150,
  "last_sync_time": "2026-07-03T03:00:00",
  "last_error": null,
  "channel_count": 0,
  "program_count": 0
}
```

### 7.2 EPG XML 生成

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/epg/xmltv.xml` | 生成完整 XMLTV 格式的 EPG XML |
| `GET` | `/epg.xml` | 快捷路由，方便 IPTV 客户端直接订阅 |

**响应**：`Content-Type: application/xml`，UTF-8 编码。

### 7.3 节目查询

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/epg/programs` | 查询节目单（分页） |
| `GET` | `/api/epg/programs/now` | 当前正在播放的节目 |

**`GET /api/epg/programs` 参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `channel_id` | string | 按频道 ID 筛选 |
| `date` | string | 按日期筛选 `YYYY-MM-DD` |
| `keyword` | string | 节目名关键词搜索 |
| `page` | int | 页码，默认 1 |
| `limit` | int | 每页条数，默认 50 |

### 7.4 统计

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/epg/stats` | EPG 数据统计 |

```json
{
  "total_programs": 52340,
  "total_channels": 150,
  "date_range": { "earliest": "2026-07-03", "latest": "2026-07-09" },
  "last_sync_time": "2026-07-03T03:00:00"
}
```

---

## 八、EPG 配置集成

### 8.1 直播模块联动

M3U 生成时，`#EXTM3U x-tvg-url` 指向本服务生成的 EPG XML：

```
#EXTM3U x-tvg-url="http://192.168.1.3:8880/epg.xml"
```

当前 `live_config.epg_url` 是用户手动填写的外部 EPG 地址。方案：
- 新增配置项 `epg_use_internal`（默认 1），启用时 M3U 的 `x-tvg-url` 自动指向 `/epg.xml`
- 保留 `epg_url` 配置项作为外部 EPG 的 fallback（`epg_use_internal=0` 时使用）

### 8.2 M3U 生成修改

在 `src/api/live.py` 的 `generate_m3u()` 函数中：

```python
configs = get_live_configs()
if configs.get("epg_use_internal") == "1":
    x_tvg_url = f"http://{lan_ip}:8880/epg.xml"
else:
    x_tvg_url = configs.get("epg_url", "")
```

---

## 九、Web UI 设计

### 9.1 侧边栏新增

```
📺 直播频道管理
📅 EPG 节目单      ← 新增
📋 系统日志
```

### 9.2 EPG 节目单页面布局

```
┌──────────────────────────────────────────────────────────────┐
│ EPG 电子节目单                            [🔄 同步] [⚙️ 设置] │
├──────────────────────────────────────────────────────────────┤
│ 同步状态：✅ 上次同步 03:00，150 频道 / 52,340 个节目          │
│ EPG XML 地址：http://192.168.1.3:8880/epg.xml    [📋 复制]  │
├──────────────────────────────────────────────────────────────┤
│ 频道：[全部 ▾]  日期：[2026-07-03 ▾]  搜索：[________] [🔍]  │
├──────────────────────────────────────────────────────────────┤
│ #  │ 频道        │ 开始时间 │ 结束时间 │ 节目名称              │
│────│─────────────│──────────│──────────│──────────────────────│
│ 1  │ CCTV-1 综合  │ 07:00    │ 07:30    │ 朝闻天下              │
│ 2  │ CCTV-1 综合  │ 07:30    │ 08:00    │ 天气预报              │
│ ... │             │          │          │                      │
├──────────────────────────────────────────────────────────────┤
│                    [◀ 上一页] 1/200 [下一页 ▶]                │
└──────────────────────────────────────────────────────────────┘
```

### 9.3 EPG 设置弹窗

```
┌──────────────────────────────────────────┐
│ EPG 模块设置                              │
├──────────────────────────────────────────┤
│ 同步天数:       [7                   ] ▾  │
│ [✓] 每日凌晨自动同步                      │
│ [✓] M3U 中使用内置 EPG XML               │
│ 外部 EPG 地址:  [                    ]    │
│                              [保存] [取消] │
└──────────────────────────────────────────┘
```

---

## 十、实施计划

### Phase 1：数据层 + 同步核心

- [ ] `src/auth/simulator.py`：新增 `get_tvod_program_list()` 方法（修正字段名 `time`/`endtime`）
- [ ] `src/db/models.py`：`epg_programs` 建表 + 索引 + 默认配置项
- [ ] `src/sync/epg_sync.py`：全量同步逻辑（后台线程 + 状态暴露）
- [ ] `POST /api/epg/sync` + `GET /api/epg/sync/status` API

### Phase 2：EPG XML 生成

- [ ] `src/api/epg.py`：XMLTV XML 生成 + `GET /epg.xml` 快捷路由
- [ ] `src/api/live.py`：M3U 生成中的 `x-tvg-url` 指向内部 EPG XML
- [ ] `main.py`：注册 EPG 路由 + 依赖注入

### Phase 3：节目查询 API

- [ ] `GET /api/epg/programs` 分页查询
- [ ] `GET /api/epg/programs/now` 当前节目
- [ ] `GET /api/epg/stats` 统计

### Phase 4：Web UI

- [ ] 侧边栏新增"EPG 节目单"入口
- [ ] 节目单浏览页面（频道/日期筛选 + 分页）
- [ ] 同步状态展示 + 手动触发同步按钮
- [ ] EPG XML 订阅地址展示 + 复制按钮
- [ ] EPG 设置弹窗

---

## 十一、待讨论 / 后续迭代

1. **定时自动同步**：Phase 1 先用 `APScheduler` 或简单 `threading.Timer` 实现每日凌晨执行
2. **节目简介拉取**：`Action=programInfo` 可以拿到 `introduce` 字段，但每个节目多一次请求，Phase 1 默认不开启（`epg_include_description=0`）
3. **增量同步 vs 全量同步**：当前全量同步约 5~10 分钟，数据量不大，Phase 1 先全量覆盖
4. **GZip 压缩输出**：`/epg.xml.gz`，减少 IPTV 客户端网络传输压力
5. **多 EPG 源支持**：后续可支持导入外部 EPG XML（如 `epg.51zmt.top`），与内部 EPG 合并去重
6. **频道自动匹配**：利用 `live_channel_aliases` 表，服务器频道名与 EPG 台名自动模糊匹配

---

## 附录 A：与直播模块的关系

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ live_channels │────▶│ epg_programs │────▶│  epg.xml     │
│ (频道主表)     │     │ (节目单)      │     │ (XMLTV 输出) │
└──────┬───────┘     └──────────────┘     └──────┬───────┘
       │                                         │
       │  tvg-id="CCTV1"                         │  <channel id="CCTV1">
       │  tvg-name="CCTV1综合高清"                │  <display-name>CCTV-1综合</display-name>
       │                                         │
       ▼                                         │
┌──────────────┐                                 │
│   tv.m3u     │◀──── x-tvg-url ────────────────┘
│ (M3U 输出)   │
└──────────────┘
```

核心原则：**M3U 的 `tvg-id` = EPG XML 的 `<channel id>` = `normalize_epg(live_channels.name)`**

## 附录 B：典型同步请求序列

```
同步开始
  │
  ├─ [频道 1/150] 浙江卫视高清 (channel_id=3844)
  │   ├─ GET data.jsp?Action=channelProgramList&channelId=3844&date=20260703 → 49 条
  │   ├─ GET data.jsp?Action=channelProgramList&channelId=3844&date=20260704 → 48 条
  │   ├─ ... × 7 天
  │   └─ 写入 ~340 条 → epg_programs
  │
  ├─ [频道 2/150] CCTV1综合高清 (channel_id=4646)
  │   └─ ... (同上)
  │
  ├─ ...
  │
  └─ [清理] DELETE FROM epg_programs WHERE end_time < '2026-07-03 00:00:00'
     └─ 完成！channel_count=150, program_count=52340, 耗时 8 分 23 秒
```

---

## 附录 C：探针实测数据汇总

> **探针脚本**：`scratch/probe_epg_*.py`（共 7 个）
> **测试日期**：2026-07-03，165 个频道全量

### C.1 清晰度合并

同一频道多种清晰度（4K/HD/SD）的 `channelProgramList` 节目数据**完全相同**（实测浙江卫视、中央一套等 31 组 HD+SD 配对，时间与名称 100% 一致）。

**4K 频道无 EPG 数据**：`channelProgramList` 对 4K 频道（`channel_id=136086102` 等）全部返回 0 条。

合并策略：按 `normalize_epg(name)` 归一化，选 HD 版 `channel_id` 作为拉取源，4K/SD 共享同一份 EPG。

### C.2 时移长度与 EPG 边界

`timeshift_length` **直接决定 EPG 历史数据保留天数**，非统一值：

| timeshift_length | 频道数 | 去重后 | 有效边界 | 典型频道 |
|:---:|:---:|:---:|------|------|
| **14400** | 36 | 24 | **-6d ~ +1d（8天）** | CCTV-1、浙江卫视、所有卫视频道 |
| **7200** | 43 | 33 | **-2d ~ +1d（4天）** | 央广购物、中央戏曲、中央新闻、部分地方台 |
| **3600** | 1 | 1 | **今天极少(4条)，+1d 完整(42条)** | CGTN |
| **120** | 5 | 5 | **无 EPG 数据** | 宁波综合/生活/测试台 |
| **60** | 5 | 5 | **无 EPG 数据** | 好易购、宁波影视/生活 |

> `len=120/60` 虽有 `timeshift=1` 但三个 EPG 接口均返回空。同步时归入"无 EPG"类。

### C.3 非时移频道的意外发现

对全部 73 个 `timeshift=0` 频道逐一探测 `channelProgramList`（今天 + 明天），发现 **33 个有数据**：

| 组别 | 频道（去重后~27个） | 今天 | 明天 |
|------|------|:--:|:--:|
| 央视 | CCTV-3/6/8 | 2~4 | 26~41 |
| 卫视 | 吉林/云南/河北/黑龙江/海南/青海/陕西/广西/内蒙古/甘肃/西藏/宁夏/兵团 | 7~95 | 21~98 |
| 上海文广 | 东方财经/动漫秀场/乐游/金色学堂/生活时尚/都市剧场/游戏风云/多彩文体4K | 1~13 | 17~120 |
| 其他 | 卡酷少儿/中国教育一套/北京纪实科教/快乐垂钓/导视频道 | 3~66 | 12~100 |

> **规律**：非时移频道今天数据极少（2~13 条），明天数据完整（30~120 条）。
> EPG 数据是在"前一天"批量加载的，并非实时覆盖当天。

真正无 EPG 的频道（40 个）：CCTV-5(高+标)、CGTN 各语种、4K测试、交通之声、浙江之声、东方卫视4K、中国教育4套等。

### C.4 最终同步频道汇总

| 来源 | 去重频道数 | 初首次同步 | 日常同步 |
|------|:---:|------|------|
| timeshift=14400 (卫视/CCTV等) | 24 | -7d ~ +1d (9天) | 今+明 |
| timeshift=7200 (购物/戏曲等) | 33 | -2d ~ +1d (4天) | 今+明 |
| timeshift=3600 (CGTN) | 1 | 今+明 | 今+明 |
| timeshift=0 有数据 (CCTV-3/6/8等) | 27 | 今+明 | 今+明 |
| **合计** | **85** | **~350次 / ~1.2分钟** | **~170次 / ~0.6分钟** |

### C.5 探测脚本清单

| 脚本 | 探测内容 |
|------|---------|
| `probe_epg_api.py` | 首次验证 3 个 EPG 接口可用性及返回格式 |
| `probe_epg_range.py` | CCTV-1 HD vs SD 日期范围 + 时间一致性 |
| `probe_epg_boundary.py` | 浙江卫视 4K/HD/SD 三版本边界 + 一致性 |
| `probe_epg_notimeshift.py` | 单个非时移频道（CCTV-5）三个接口测试 |
| `probe_epg_channel_stats.py` | 全频道 timeshift 分布 + 合并去重统计 |
| `probe_epg_timeshift_boundary.py` | 3 种 timeshift_length 的边界差异化分析 |
| `probe_epg_timeshift2.py` | 补充测试 len=120/60 频道边界 |
| `probe_epg_notimeshift_all.py` | 全部 73 个非时移频道逐一探测 |

---

## 更新记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-07-03 | 1.2 | 发现 VIS `api/schedules/` 接口：覆盖全频道、无需认证。同步方案切换。 |
| 2026-07-03 | 1.1 | 基于 7 轮探针实测：时移长度-边界关联、非时移频道 EPG 发现、合并策略 |
| 2026-07-03 | 1.0 | 初始版本 |
