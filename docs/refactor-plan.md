# IPTV-Toolkit 重构计划

> 创建时间：2026-07-01  
> 目标：将原有单文件架构重构为模块化架构，引入本地 SQLite 数据库，对接 `api/search/filter.json` 接口

---

## 一、背景与动机

### 现有问题

1. **`vod-api.py` 单文件过大**（1410 行），职责混乱
2. **无本地数据库**：每次请求实时调 VIS API，翻页慢，不支持搜索
3. **`MemoryLogBuffer` 复杂**：内存日志缓冲，重启即丢失
4. **分类管理繁琐**：需手动拖拽映射 VIS 分类到 TVBox 过滤器
5. **`filter.json` 接口未利用**：该接口支持按类型/评分排序，数据量大，适合本地缓存

### 重构目标

- 模块化：按职责拆分成独立模块，每个模块可单独测试
- 本地数据库：SQLite 存储 `filter.json` 全量数据，支持快速搜索/过滤
- 简化过滤：硬编码过滤器配置（`country`/`year`/`score`），无需手动映射
- 标准日志：用 Python `logging` 模块写文件，Web UI 读取展示
- 保留认证：STB 登录/心跳逻辑完整保留（播放时仍需要）

---

## 二、新架构设计

### 目录结构

```
IPTV-Toolkit/
├── main.py                    # 应用入口，组装所有模块，启动 FastAPI
├── src/
│   ├── __init__.py
│   ├── auth/                  # Network & Auth 模块
│   │   ├── __init__.py
│   │   ├── config.py          # STBDeviceConfig（从 run_simulator.py 迁移）
│   │   ├── state.py           # STBRuntimeState（从 run_simulator.py 迁移）
│   │   ├── simulator.py       # STBSimulator（从 run_simulator.py 迁移）
│   │   └── heartbeat.py       # 心跳线程管理
│   ├── sync/                  # 数据同步模块
│   │   ├── __init__.py
│   │   └── filter_sync.py     # filter.json → SQLite 同步逻辑
│   ├── db/                    # Data Storage 模块
│   │   ├── __init__.py
│   │   ├── models.py          # 表结构定义 + 初始化
│   │   └── crud.py            # 增删改查操作
│   ├── api/                   # API Service 模块
│   │   ├── __init__.py
│   │   ├── tvbox.py           # TVBox 协议接口（查本地库）
│   │   └── play.py            # 播放地址解析（contentCode → play_url）
│   ├── web/                   # Web UI 模块
│   │   ├── __init__.py
│   │   └── routes.py          # Web API 路由（配置、日志、同步状态）
│   └── utils/                 # 工具模块
│       ├── __init__.py
│       ├── logger.py          # logging 模块封装
│       └── helpers.py         # 工具函数（parse_epg_json 等）
├── static/                    # Web UI 前端
│   ├── index.html
│   ├── js/
│   │   └── app.js             # 改造后的前端逻辑
│   └── css/
│       └── style.css
├── data/
│   ├── iptv.db                # SQLite 数据库（自动创建）
│   └── stb_config.json        # STB 配置（保留原格式）
├── requirements.txt
├── Dockerfile                  # 保留（如需 Docker 部署）
└── readme.md                  # 更新说明
```

---

## 三、数据库设计

### `vod_items` 表

```sql
CREATE TABLE IF NOT EXISTS vod_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    contentCode     TEXT UNIQUE,      -- VIS 内容编码（重要！播放时用）
    title           TEXT,             -- 标题
    type            TEXT,             -- 大类：电视剧/电影/综艺/动漫/少儿
    contentType     TEXT,             -- series / vod（TVBox 用）
    year            TEXT,             -- 年份
    country         TEXT,             -- 国家/地区
    actors          TEXT,             -- 主演
    director        TEXT,             -- 导演
    score           REAL,             -- 评分
    icon            TEXT,             -- 小图 URL
    poster          TEXT,             -- 大图 URL
    isFinished      INTEGER,          -- 是否完结（0/1）
    episodeTotal    INTEGER,          -- 总集数
    contentBaseType TEXT,             -- 002=电视剧, 001=电影
    contentBaseTags TEXT,             -- 内容标签码（如 "0114"）
    syncedAt        INTEGER           -- 同步时间戳（用于全量覆盖判断）
);

-- 索引（加速搜索和过滤）
CREATE INDEX IF NOT EXISTS idx_title ON vod_items(title);
CREATE INDEX IF NOT EXISTS idx_type ON vod_items(type);
CREATE INDEX IF NOT EXISTS idx_country ON vod_items(country);
CREATE INDEX IF NOT EXISTS idx_year ON vod_items(year);
CREATE INDEX IF NOT EXISTS idx_score ON vod_items(score);
```

### 同步策略

- **首次同步**：全量拉取所有 `type`（电视剧/电影/综艺/动漫/少儿）
- **每日同步**：全量覆盖（先标记 `syncedAt`，再删过期数据）
- **分页参数**：`size=50`，`pageindex=0,1,2...`（直到 `pageInfo.pageCount`）

---

## 四、TVBox 接口设计

### 过滤器配置（硬编码）

```python
FILTER_CONFIG = {
    "movies": {
        "name": "电影专区",
        "filters": [
            {
                "key": "country",
                "name": "地区",
                "value": ["美国", "内地", "日本", "韩国", "英国", "其他"]
            },
            {
                "key": "year", 
                "name": "年份",
                "value": ["2024", "2023", "2020-2029", "2010-2019", "2000-2009", "更早"]
            }
        ]
    },
    "series": {
        "name": "电视剧场",
        "filters": [
            {
                "key": "country",
                "name": "地区",
                "value": ["美国", "内地", "日本", "韩国", "英国", "其他"]
            },
            {
                "key": "year",
                "name": "年份",
                "value": ["2024", "2023", "2020-2029", "2010-2019", "2000-2009", "更早"]
            }
        ]
    }
}
```

### 多条件过滤 SQL 构建

```python
def build_filter_sql(f_filters: dict, content_type: str, page: int):
    """根据 TVBox 传入的 f 参数，构建 SQL WHERE 条件"""
    sql = "SELECT * FROM vod_items WHERE type = ?"
    params = [content_type]
    
    if f_filters.get("country"):
        sql += " AND country = ?"
        params.append(f_filters["country"])
    
    if f_filters.get("year"):
        year_range = f_filters["year"]
        if "-" in year_range:
            y1, y2 = year_range.split("-")
            sql += " AND CAST(year AS INT) BETWEEN ? AND ?"
            params.extend([y1, y2])
        else:
            sql += " AND year = ?"
            params.append(year_range)
    
    # 默认按评分降序
    sql += " ORDER BY score DESC"
    sql += " LIMIT 20 OFFSET ?"
    params.append((page - 1) * 20)
    
    return sql, params
```

### TVBox 协议兼容

| 参数 | 说明 | 实现 |
|------|------|------|
| `ac=list&t=series` | 获取分类列表 | 查 SQLite，支持 `f` 参数过滤 |
| `ac=detail&ids=series_xxx` | 获取详情 | `contentCode → vodIdByCode → 播放地址` |
| `ac=list&wd=关键词` | 搜索 | SQLite `WHERE title LIKE %关键词%` |
| `f=Base64(JSON)` | 过滤器 | 解析后构建 SQL WHERE |

---

## 五、播放链路原理

```
contentCode (VIS 内容编码)
    ↓  vodIdByCode 接口
vod_id (EPG 内部资源 ID)
    ↓  get_vod_play_url 接口
播放 URL (媒体服务器地址，带时效 token)
```

### 为什么不能预缓存 vod_id？

- `vod_id` 可能因 EPG 服务器切换而变化
- 播放 URL 带时效性 token（每次请求不同）
- **每次播放时必须实时解析**

### 实现位置

- `src/api/play.py`：封装 `contentCode → play_url` 完整链路
- TVBox 的 `vod_play_url` 字段填充分集信息时使用代理地址

---

## 六、Web UI 改造

### 保留功能

1. **STB 配置**（原 `stb` tab）
   - 设备参数表单
   - 登录状态展示
   - 保存并测试登录

2. **同步状态**（新增 `sync` tab）
   - 手动触发同步按钮
   - 同步进度展示
   - 上次同步时间
   - 数据库统计（总条数、各类型数量）

3. **日志查看**（原 `stb` tab 底部）
   - 读取 `data/iptv_toolkit.log` 文件
   - 支持级别过滤
   - 自动滚动

### 移除功能

- ~~VOD 分类映射~~（不再需要，过滤器硬编码）
- ~~源树浏览~~（本地数据无需实时爬分类树）
- ~~MemoryLogBuffer~~（改用标准 logging）

### 前端 tab 规划

```
sidebar nav:
  - ⚙️ 系统凭证配置 (stb)     — 保留
  - 🔄 数据同步管理 (sync)     — 新增
  - 📋 系统日志 (log)          — 从原 stb tab 底部移出，独立 tab
```

---

## 七、模块实现顺序

### Step 1: 工具模块 `src/utils/`

**文件**：`src/utils/logger.py`

- 封装 `logging` 模块
- 输出到 `data/iptv_toolkit.log`
- 同时输出到控制台

**文件**：`src/utils/helpers.py`

- `parse_epg_json(text)` — 从 `run_simulator.py` 迁移
- `get_iptv_local_ip()` — 从 `run_simulator.py` 迁移

**测试**：
```bash
python -c "from src.utils.logger import logger; logger.info('test')"
# 检查 data/iptv_toolkit.log 是否有内容
```

---

### Step 2: 数据库模块 `src/db/`

**文件**：`src/db/models.py`

- `get_db_connection()` — 获取 SQLite 连接
- `init_db()` — 创建表结构
- `DB_PATH` — 数据库文件路径

**文件**：`src/db/crud.py`

- `bulk_upsert_items(items, type_name)` — 批量插入/更新
- `search_items(keyword, page)` — 搜索
- `filter_items(content_type, filters, page)` — 过滤查询
- `get_item_by_code(content_code)` — 根据 contentCode 查询单条
- `get_filter_options()` — 获取过滤器可选值（用于 TVBox 初始化）
- `clean_old_data(synced_at)` — 删除过期数据（全量覆盖用）
- `get_stats()` — 统计信息（总条数、各类型数量）

**测试**：
```bash
python -c "
from src.db.models import init_db
from src.db.crud import get_stats
init_db()
print(get_stats())
"
```

---

### Step 3: 认证模块 `src/auth/`

**文件**：`src/auth/config.py`

- `STBDeviceConfig` 类（从 `run_simulator.py` 迁移）

**文件**：`src/auth/state.py`

- `STBRuntimeState` 类（从 `run_simulator.py` 迁移）

**文件**：`src/auth/simulator.py`

- `STBSimulator` 类（从 `run_simulator.py` 迁移）
- 保留方法：`login()`, `keep_alive()`, `get_vod_play_url()`, `get_series_info()`
- 移除方法：`get_channel_list()`, `get_tvod_program_list()`（本次重构不需要）

**文件**：`src/auth/heartbeat.py`

- `start_heartbeat_thread(simulator)` — 心跳线程启动
- `ensure_authenticated(simulator)` — 确保认证状态

**测试**：
```bash
python -c "
from src.auth.config import STBDeviceConfig
from src.auth.simulator import STBSimulator
import json
with open('data/stb_config.json') as f:
    cfg = json.load(f)
config = STBDeviceConfig(...)
sim = STBSimulator(config)
print(sim.login())
"
```

---

### Step 4: 同步模块 `src/sync/`

**文件**：`src/sync/__init__.py`

- `full_sync(simulator)` — 全量同步入口
- `sync_filter_data(simulator, type_name)` — 同步单个类型

**逻辑**：
1. 检查 `simulator.state.vis_base_url` 是否解析
2. 分页请求 `filter.json`
3. 调用 `crud.bulk_upsert_items()` 写入数据库
4. 调用 `crud.clean_old_data()` 删除过期数据

**后台任务**：
- 用 `threading.Thread` 执行，避免阻塞 API
- 同步状态存储在内存变量中（供 Web UI 查询）

**测试**：
```bash
# 需要先完成 Step 1-3
python -c "
from src.sync import full_sync
# 需要先登录
full_sync(sim)
print('同步完成')
"
```

---

### Step 5: API 模块 `src/api/`

**文件**：`src/api/tvbox.py`

- `handle_tvbox_request(request)` — TVBox 协议主入口
  - `ac=list&t=xxx` — 分类列表（查 SQLite）
  - `ac=detail&ids=xxx` — 详情（实时解析播放地址）
  - `ac=list&wd=xxx` — 搜索（查 SQLite）
- `get_tvbox_config(request)` — 返回 TVBox 配置文件（`/zjvod`）
- `get_filters()` — 返回过滤器配置

**文件**：`src/api/play.py`

- `resolve_play_url(vod_id_or_code)` — 播放地址解析
  - 解析 `vod_id_or_code` 格式：`{contentType}_{contentCode}`
  - 调用 `simulator.get_vod_play_url()`

**测试**：
```bash
# 启动服务后
curl "http://localhost:8880/api/vod?ac=list&t=series"
curl "http://localhost:8880/api/vod?ac=list&wd=白"
```

---

### Step 6: Web 路由模块 `src/web/`

**文件**：`src/web/routes.py`

- `GET /api/stb-config` — 获取 STB 配置
- `POST /api/stb-config` — 保存 STB 配置并测试登录
- `GET /api/sim-status` — 获取认证状态
- `POST /api/sync/start` — 触发同步
- `GET /api/sync/status` — 获取同步状态
- `GET /api/sync/stats` — 获取数据库统计
- `GET /api/logs` — 获取日志文件内容
- `POST /api/logs/clear` — 清空日志文件

---

### Step 7: 入口文件 `main.py`

**职责**：
1. 创建 FastAPI 应用
2. 注册路由：
   - `src.web.routes` 的 Web API
   - `src.api.tvbox` 的 TVBox API
   - `src.api.play` 的播放解析 API
3. 启动时初始化：
   - `init_db()`
   - `login_sim()`
   - 启动心跳线程
4. 挂载静态文件

**测试**：
```bash
python main.py
# 浏览器访问 http://localhost:8880
```

---

### Step 8: 前端改造 `static/`

**`index.html`**：
- 移除 VOD 分类映射 tab
- 新增数据同步管理 tab
- 日志面板改为读取文件

**`js/app.js`**：
- 新增 `sync` tab 的 Vue 逻辑
- 改造日志读取逻辑（`/api/logs` 改为读文件）
- 移除拖拽相关代码

---

## 八、测试计划

每个模块完成后执行：

### Step 1 测试
- [ ] `logger.py` 能写文件
- [ ] `helpers.py` 的 `parse_epg_json` 能解析非标准 JSON

### Step 2 测试
- [ ] `init_db()` 能创建数据库文件
- [ ] `bulk_upsert_items()` 能插入数据
- [ ] `search_items()` 能搜索
- [ ] `filter_items()` 能过滤

### Step 3 测试
- [ ] `STBSimulator.login()` 能成功登录
- [ ] 心跳线程能正常启动

### Step 4 测试
- [ ] `full_sync()` 能拉取数据并写入数据库
- [ ] 同步状态能正确展示

### Step 5 测试
- [ ] TVBox 配置文件能正常返回
- [ ] 分类列表能正常返回
- [ ] 搜索能正常工作
- [ ] 播放解析能正常工作

### Step 6 测试
- [ ] Web UI 能正常加载
- [ ] STB 配置能保存
- [ ] 同步状态能展示
- [ ] 日志能展示

### Step 7 测试
- [ ] `main.py` 能正常启动
- [ ] 所有 API 能正常访问

### Step 8 测试
- [ ] 前端能正常显示
- [ ] 所有功能能正常使用

---

## 九、回退方案

如果重构过程中出现问题：

1. **保留原文件**：重构前备份 `vod-api.py` 和 `run_simulator.py`
2. **渐进式切换**：可以先让新版本监听不同端口（如 8881），验证通过后再切换
3. **Docker 回退**：`Dockerfile` 保留，可回退到旧版本镜像

---

## 十、后续优化（重构完成后）

1. **`contentBaseTags` 映射**：写脚本统计实际数据，建立标签编码 → 类型名称的对照表
2. **定时同步**：用 `schedule` 或 `APScheduler` 实现每日自动同步
3. **分集信息缓存**：同步时预拉取热门剧集的分集信息
4. **Web UI 优化**：用 Vue 3 重构前端（可选）

---

## 附录 A：关键接口对照表

| 原文件 | 原行号 | 目标模块 | 说明 |
|--------|--------|----------|------|
| `run_simulator.py` | 106-154 | `src/auth/config.py` | `STBDeviceConfig` |
| `run_simulator.py` | 156-184 | `src/auth/state.py` | `STBRuntimeState` |
| `run_simulator.py` | 190-825 | `src/auth/simulator.py` | `STBSimulator` |
| `vod-api.py` | 1-130 | `src/utils/logger.py` | 日志（重写） |
| `vod-api.py` | 132-172 | `src/auth/` | 认证初始化 |
| `vod-api.py` | 177-227 | `src/auth/heartbeat.py` | 心跳 |
| `vod-api.py` | 396-624 | `src/sync/filter_sync.py` | VIS API 请求（参考） |
| `vod-api.py` | 838-1140 | `src/api/tvbox.py` | TVBox API（改造） |
| `vod-api.py` | 1145-1210 | `src/api/play.py` | 播放解析 |
| `vod-api.py` | 1219-1408 | `src/web/routes.py` | Web 路由（改造） |

---

## 附录 B：配置文件格式

### `data/stb_config.json`（保持不变）

```json
{
    "user_id": "",
    "stb_id": "",
    "mac_address": "",
    "ip_address": "",
    "base_url": "",
    "des_key": ""
}
```

### `data/iptv_toolkit.log`（新增）

标准 logging 格式：
```
2026-07-01 10:30:00 [INFO] 模拟机顶盒上线成功
2026-07-01 10:30:01 [INFO] VIS VOD 服务器: http://115.233.200.60:58000/epg/
```

---

*重构计划结束*
