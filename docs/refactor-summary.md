# IPTV-Toolkit 重构总结

> 重构完成时间：2026-07-01
> 原始架构：`vod-api.py`（1410 行单文件）+ `run_simulator.py`（826 行）

---

## 一、重构成果

### 全部 8 个步骤测试通过

| 步骤 | 模块 | 文件 | 状态 |
|------|------|------|------|
| Step 1 | 工具模块 | `src/utils/logger.py`, `helpers.py` | ✅ |
| Step 2 | 数据库模块 | `src/db/models.py`, `crud.py` | ✅ |
| Step 3 | 认证模块 | `src/auth/config.py`, `state.py`, `simulator.py`, `heartbeat.py` | ✅ |
| Step 4 | 同步模块 | `src/sync/filter_sync.py` | ✅ |
| Step 5 | API 模块 | `src/api/tvbox.py`, `play.py` | ✅ |
| Step 6 | Web 路由 | `src/web/routes.py` | ✅ |
| Step 7 | 入口文件 | `main.py` | ✅ |
| Step 8 | 前端改造 | `static/index.html`, `js/app.js` | ✅ |

---

## 二、新文件结构

```
IPTV-Toolkit/
├── main.py                  # 入口（替代 vod-api.py）
├── src/
│   ├── utils/
│   │   ├── logger.py        # 标准 logging（替代 MemoryLogBuffer）
│   │   └── helpers.py       # parse_epg_json, get_iptv_local_ip
│   ├── db/
│   │   ├── models.py        # SQLite 表结构
│   │   └── crud.py          # 增删改查（支持多条件过滤）
│   ├── auth/
│   │   ├── config.py        # STBDeviceConfig
│   │   ├── state.py         # STBRuntimeState
│   │   ├── simulator.py     # STBSimulator（登录/心跳/播放）
│   │   └── heartbeat.py     # 心跳线程
│   ├── sync/
│   │   └── filter_sync.py   # filter.json → SQLite 同步
│   ├── api/
│   │   ├── tvbox.py         # TVBox 协议（查本地库 + 多条件过滤）
│   │   └── play.py          # 播放地址解析
│   └── web/
│       └── routes.py        # Web API（配置/同步/日志）
├── static/                  # Web UI（精简版）
└── data/
    ├── iptv.db              # SQLite 数据库
    └── stb_config.json      # STB 配置

```

---

## 三、关键改进

| 原方案 | 新方案 |
|--------|--------|
| `MemoryLogBuffer` 内存缓冲，重启丢失 | Python `logging` 写 `data/iptv_toolkit.log`，Web UI 实时读取 |
| 每次请求实时调 VIS API，翻页慢 | SQLite 本地缓存全量数据，毫秒级查询 |
| 手动拖拽映射 VIS 分类到 TVBox 过滤器 | 硬编码 `FILTER_CONFIG`，`country`/`year` 多条件过滤 |
| 单文件 1410 行 | 17 个模块文件，每个 < 400 行，职责清晰 |
| 无搜索功能 | SQLite `LIKE` 搜索标题/演员/导演 |

---

## 四、TVBox 接口兼容

| 参数 | 说明 | 实现 |
|------|------|------|
| `ac=list&t=series` | 获取分类列表 | 查 SQLite，支持 `f` 参数过滤 |
| `ac=detail&ids=series_xxx` | 获取详情 | `contentCode → vodIdByCode → 播放地址` |
| `ac=list&wd=关键词` | 搜索 | SQLite `WHERE title LIKE %关键词%` |
| `f=Base64(JSON)` | 多条件过滤 | 解析后构建 SQL WHERE |

### 过滤器配置

```python
FILTER_CONFIG = {
    "movies": [
        {
            "key": "country", "name": "地区",
            "value": [
                {"n": "全部", "v": ""},
                {"n": "美国", "v": "美国"},
                {"n": "内地", "v": "内地"},
                {"n": "日本", "v": "日本"},
                {"n": "韩国", "v": "韩国"},
                {"n": "英国", "v": "英国"},
                {"n": "其他", "v": "其他"}
            ]
        },
        {
            "key": "year", "name": "年份",
            "value": [
                {"n": "全部", "v": ""},
                {"n": "2024", "v": "2024"},
                {"n": "2023", "v": "2023"},
                {"n": "2020-2029", "v": "2020-2029"},
                {"n": "2010-2019", "v": "2010-2019"},
                {"n": "2000-2009", "v": "2000-2009"},
                {"n": "更早", "v": "1900-1999"}
            ]
        }
    ],
    "series": [...]  # 同上
}
```

> **2026-07-01 更新**：value 从字符串数组改为 `{n, v}` 对象格式（TVBox 标准），增加"全部"选项，修正"更早"为年份区间 `1900-1999`。分类列表响应亦补上 `filters` 字段以兼容更多 TVBox 版本。

---

## 五、数据库设计

### `vod_items` 表

```sql
CREATE TABLE IF NOT EXISTS vod_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    contentCode     TEXT UNIQUE,      -- VIS 内容编码（播放时必须）
    title           TEXT,             -- 标题
    type            TEXT,             -- 大类：电视剧/电影/综艺/动漫/少儿
    contentType     TEXT,             -- series / vod
    year            TEXT,             -- 年份
    country         TEXT,             -- 国家/地区
    actors          TEXT,             -- 主演
    director        TEXT,             -- 导演
    score           REAL,             -- 评分
    icon            TEXT,             -- 小图 URL
    poster          TEXT,             -- 大图 URL
    isFinished      INTEGER,         -- 是否完结
    episodeTotal    INTEGER,         -- 总集数
    contentBaseType TEXT,             -- 002=电视剧, 001=电影
    contentBaseTags TEXT,             -- 内容标签码
    syncedAt        INTEGER           -- 同步时间戳
);
```

---

## 六、播放链路（保持不变）

```
contentCode (VIS 内容编码)
    ↓  vodIdByCode 接口
vod_id (EPG 内部资源 ID)
    ↓  get_vod_play_url 接口
播放 URL (媒体服务器地址，带时效 token)
```

---

## 七、Web UI 改造

### Tab 布局

| Tab | 功能 | 说明 |
|-----|------|------|
| 系统凭证配置 | STB 设备参数 + 登录状态 | 保留 |
| 数据同步管理 | 手动触发同步 + 数据库统计 | 新增 |
| 系统日志 | 读取 `iptv_toolkit.log`，支持级别过滤 | 从原 stb tab 底部独立出来 |

### 移除功能

- ~~VOD 分类映射~~（不再需要，过滤器硬编码）
- ~~源树浏览~~（本地数据无需实时爬分类树）
- ~~MemoryLogBuffer~~（改用标准 logging）

---

## 八、启动方式

```bash
python main.py
# 浏览器访问 http://localhost:8880/settings
# TVBox 接口: http://localhost:8880/api/vod
# TVBox 配置: http://localhost:8880/zjvod
```

---

## 九、后续优化方向

1. **`contentBaseTags` 映射**：写脚本统计实际数据，建立标签编码 → 类型名称对照表
2. **定时同步**：用 `APScheduler` 实现每日自动同步
3. **分集信息缓存**：同步时预拉取热门剧集的分集信息
4. **Web UI 优化**：考虑用 Vue 3 重构前端

---

## 十、API 路由汇总

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 重定向到 `/settings` |
| GET | `/settings` | Web UI 主页面 |
| GET | `/zjvod` | TVBox 配置文件 |
| GET | `/api/vod` | TVBox 协议接口 |
| GET | `/api/play` | 播放地址解析 |
| GET | `/api/stb-config` | 获取 STB 配置 |
| POST | `/api/stb-config` | 保存 STB 配置 |
| GET | `/api/sim-status` | 获取认证状态 |
| POST | `/api/sync/start` | 触发同步 |
| GET | `/api/sync/status` | 获取同步状态 |
| GET | `/api/sync/stats` | 获取数据库统计 |
| GET | `/api/logs` | 获取日志 |
| POST | `/api/logs/clear` | 清空日志 |
