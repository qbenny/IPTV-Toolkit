# IPTV 直播频道模块 — 需求规格说明书

> 版本: 1.0 | 最后更新: 2026-07-02

---

## 一、模块概述

本模块实现 IPTV 直播频道的全生命周期管理，包括：

1. **频道拉取**：登录后从服务器拉取频道列表（`getchannellistHWCTC.jsp`），全字段解析存入数据库
2. **外部频道**：支持导入外部整理的频道（M3U/CSV），自动去重合并
3. **Web UI**：频道浏览、分类管理、启用/禁用、自定义拖拽排序
4. **M3U 生成**：生成标准 M3U 文件，支持 udpxy 组播转HTTP、FCC加速、时移回看、组播/单播双线
5. **频道同步**：手动触发同步，全量 UPSERT + 下线频道软删除

---

## 二、数据表设计

### 2.1 频道分类表 `live_categories`

独立建表，自定义分类持久化，不受服务器频道增减影响。

```sql
CREATE TABLE IF NOT EXISTS live_categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    sort_index  INTEGER DEFAULT 0,
    color       TEXT DEFAULT '',
    is_visible  INTEGER DEFAULT 1,
    created_at  INTEGER DEFAULT 0
);
```

**预置分类**（首次建表插入）：
`央视高清`、`央视标清`、`卫视高清`、`卫视标清`、`地方高清`、`地方标清`、`4K超高清`、`国际`、`付费高清`、`广播`、`其他`

### 2.2 频道主表 `live_channels`

存储所有频道（服务器下发 + 外部导入），`source` 区分来源。

```sql
CREATE TABLE IF NOT EXISTS live_channels (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source            TEXT NOT NULL DEFAULT 'server',       -- server / external
    channel_id        TEXT NOT NULL DEFAULT '',             -- 服务器 ChannelID，用于关联匹配
    user_channel_id   TEXT DEFAULT '',                      -- 机顶盒显示序号
    name              TEXT DEFAULT '',                      -- 频道名称（服务器下发）
    -- ▼ 用户自定义字段（同步时保护，不覆盖）
    tvg_id            TEXT DEFAULT '',                      -- EPG 匹配 ID（新频道默认复制 name）
    tvg_name          TEXT DEFAULT '',                      -- EPG 匹配名（新频道默认复制 name）
    logo_url          TEXT DEFAULT '',                      -- LOGO 文件名（新频道默认 = name + ".png"）
    category_id       INTEGER DEFAULT 0,                   -- FK → live_categories.id
    sort_index        INTEGER DEFAULT 0,                   -- 自定义排序索引
    is_enabled        INTEGER DEFAULT 1,                   -- 是否启用（0=禁用，不生成到M3U）
    -- ▲ 用户自定义字段结束
    -- ▼ 服务器下发字段（同步时覆盖更新）
    multicast_url     TEXT DEFAULT '',                      -- 原始组播地址 igmp://233.x.x.x:5140
    unicast_url       TEXT DEFAULT '',                      -- 简化版单播 URL（去动态参数，长期有效）
    unicast_url_full  TEXT DEFAULT '',                      -- 完整单播 URL（含 token，每次登录刷新）
    timeshift_enabled INTEGER DEFAULT 0,                   -- 是否支持时移（TimeShift=1 则为 1）
    timeshift_length  INTEGER DEFAULT 0,                   -- 时移时长（TimeShiftLength 原始值）
    timeshift_url     TEXT DEFAULT '',                      -- TimeShiftURL 原始值
    is_hd             INTEGER DEFAULT 0,                   -- IsHDChannel: 1=标清，2=高清
    channel_type      TEXT DEFAULT '',                      -- ChannelType
    channel_sdp       TEXT DEFAULT '',                      -- ChannelSDP 原始完整值
    channel_url_raw   TEXT DEFAULT '',                      -- ChannelURL 原始值（| 分隔）
    channel_locked    INTEGER DEFAULT 0,                   -- ChannelLocked
    preview_enabled   INTEGER DEFAULT 0,                   -- PreviewEnable
    fcc_enabled       INTEGER DEFAULT 0,                   -- FCCEnable（全局开关覆盖此项）
    fcc_ip            TEXT DEFAULT '',                      -- ChannelFCCIP
    fcc_port          TEXT DEFAULT '',                      -- ChannelFCCPort
    fec_port          TEXT DEFAULT '',                      -- ChannelFECPort（当前为0，预留）
    raw_fields_json   TEXT DEFAULT '',                      -- 服务器下发原始键值对 JSON 全量冗余
    -- ▲ 服务器下发字段结束
    synced_at         INTEGER DEFAULT 0,                   -- 最近同步时间戳（Unix秒）
    created_at        INTEGER DEFAULT 0                     -- 创建时间戳
);
```

**索引**：
```sql
CREATE INDEX IF NOT EXISTS idx_live_source    ON live_channels(source);
CREATE INDEX IF NOT EXISTS idx_live_category  ON live_channels(category_id);
CREATE INDEX IF NOT EXISTS idx_live_enabled   ON live_channels(is_enabled);
CREATE INDEX IF NOT EXISTS idx_live_channel_id ON live_channels(channel_id);
CREATE INDEX IF NOT EXISTS idx_live_multicast ON live_channels(multicast_url);
```

**字段设计说明**：

- **`raw_fields_json`**：存储服务器下发的原始键值对 JSON（如 `{"ChannelID":"3844","ChannelName":"浙江卫视高清",...}`），好处：
  - 向前兼容：服务器未来新增字段无需改表结构，可从 raw 直接提取
  - 排错调试：对比 raw 数据和解析结果，快速定位解析 bug
  - 数据回补：后期决定新增某个字段时，可直接从 raw 回填历史数据，无需全量重新同步
  - 成本极低：100-200 个频道 × 几百字节 ≈ 几十 KB
- **用户自定义字段**（`tvg_id`、`tvg_name`、`logo_url`、`category_id`、`sort_index`、`is_enabled`）：同步 UPSERT 时**不覆盖**，保护用户手工修改
- `resolution` 字段不存储：服务器原始数据中无此字段，外部 CSV 中的分辨率是整理者自行测试添加的，不做独立字段

### 2.3 配置表 `live_config`

```sql
CREATE TABLE IF NOT EXISTS live_config (
    key   TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
```

| key | 说明 | 示例值 |
|-----|------|--------|
| `udpxy_address` | udpxy 代理地址 | `http://192.168.1.1:6688` |
| `m3u_auth_required` | M3U 访问鉴权（当前固定0=不鉴权） | `0` |
| `fcc_global_enabled` | 全局 FCC 开关（0=关闭，1=开启） | `1` |
| `timeshift_enabled` | **全局**时移开关（0=关闭，1=开启） | `1` |
| `epg_url` | EPG XML 地址（用于 `#EXTM3U x-tvg-url`） | `http://192.168.1.3:6688/epg.xml.gz` |
| `logo_base_url` | LOGO 基础 URL | `/static/logo/` |
| `m3u_dual_line` | M3U 双线模式（0=仅组播，1=组播+单播两行） | `0` |

---

## 三、同步策略

### 3.1 服务器频道同步（全量 UPSERT + 软删除）

触发方式：`POST /api/live/sync`

流程：
1. 调用 `STBSimulator.get_channel_list()` 拉取全量频道列表
2. 获取当前时间戳作为 `sync_time`
3. 遍历返回的频道，按 `channel_id` 匹配 **UPSERT**（有则 UPDATE，无则 INSERT），同时设置 `source = 'server'`

**UPSERT 字段更新策略**：

| 字段类别 | 字段 | UPSERT 行为 |
|----------|------|-------------|
| **服务器下发** | `name`, `user_channel_id`, `multicast_url`, `unicast_url`, `unicast_url_full`, `timeshift_enabled`, `timeshift_length`, `timeshift_url`, `is_hd`, `channel_type`, `channel_sdp`, `channel_url_raw`, `channel_locked`, `preview_enabled`, `fcc_enabled`, `fcc_ip`, `fcc_port`, `fec_port`, `raw_fields_json` | **覆盖**（每次同步用最新值） |
| **用户自定义** | `tvg_id`, `tvg_name`, `logo_url`, `category_id`, `sort_index`, `is_enabled` | **不覆盖**（保护用户手工修改，仅 INSERT 新频道时填充默认值） |
| 元数据 | `synced_at` | **覆盖**（设为 `sync_time`） |
| 元数据 | `created_at` | **不覆盖**（首次 INSERT 时设置） |

**INSERT 新频道时的默认值**：
- `tvg_id` = `name`（用户可后续修改为匹配自用 EPG）
- `tvg_name` = `name`
- `logo_url` = `name` + `".png"`（如 `CCTV1综合高清.png`，用户可将缺失 LOGO 放入 `static/logo/`）
- `category_id` = `0`（未分类）
- `sort_index` = `0`
- `is_enabled` = `1`

4. 同步完成后，将 **`source='server'` 且 `synced_at != sync_time`** 的频道标记为 `is_enabled = 0`（下线频道软删除）

> 服务器频道不允许 DELETE，只能禁用。仅外部频道允许删除。

### 3.2 外部频道导入

- 支持 M3U  / CSV 格式
- 导入时按 `channel_id` + `multicast_url` 判断是否已存在，存在则跳过
- 导入后标记 `source = 'external'`
- 支持文件上传和链接粘贴两种方式

---

## 四、M3U 输出格式

### 4.1 组播模式（`m3u_dual_line=0`，默认）

```m3u
#EXTM3U x-tvg-url="http://192.168.1.3:6688/epg.xml.gz"
#EXTINF:-1 tvg-id="CCTV1综合高清" tvg-name="CCTV1综合高清" tvg-logo="http://192.168.1.3:6688/Logo/CCTV1.png" group-title="央视高清" catchup="default" catchup-source="rtsp://218.71.128.109/PLTV/.../...smil?playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}",CCTV1综合高清
http://192.168.1.1:6688/udp/233.50.201.118:5140?fcc=218.71.128.109:8027
```

### 4.2 双线模式（`m3u_dual_line=1`）

同一频道输出组播 + 单播两行，支持时移则两行都加 `catchup` 参数：

```m3u
#EXTINF:-1 ... group-title="央视高清" catchup="default" catchup-source="rtsp://...",CCTV1综合高清
http://192.168.1.1:6688/udp/233.50.201.118:5140?fcc=218.71.128.109:8027
#EXTINF:-1 ... group-title="央视高清" catchup="default" catchup-source="rtsp://...",CCTV1综合高清
rtsp://218.71.128.109/PLTV/.../...smil
```

### 4.3 catchup 输出控制逻辑

```
if live_config.timeshift_enabled == 0:
    → 全频道不输出 catchup（全局关闭）

if live_config.timeshift_enabled == 1:
    → 对每个频道判断 live_channels.timeshift_enabled:
        = 1 → 输出 catchup="default" catchup-source="..."
        = 0 → 不输出 catchup（该频道不支持时移，加时移会导致回看出错）
```

### 4.4 各字段数据来源

| M3U 属性 | 数据来源 |
|----------|----------|
| `x-tvg-url` | `live_config.epg_url` |
| `tvg-id` | `live_channels.tvg_id`（默认 = name） |
| `tvg-name` | `live_channels.tvg_name`（默认 = name） |
| `tvg-logo` | `live_config.logo_base_url` + `live_channels.logo_url` |
| `group-title` | `live_categories.name`（通过 `category_id` 关联） |
| `catchup="default"` | 全局开关 + 频道开关共同决定 |
| `catchup-source` | `live_channels.unicast_url` + `?playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}` |
| 播放地址（组播） | `{udpxy_address}/udp/{ip}:{port}?fcc={fcc_ip}:{fcc_port}` |
| 播放地址（单播） | `live_channels.unicast_url`（简化版，不含动态参数） |

**地址转换规则**：
- 组播 `igmp://233.50.201.100:5140` → `http://{udpxy_address}/udp/233.50.201.100:5140`
- FCC 参数仅在 `live_config.fcc_global_enabled=1` 时追加 `?fcc={fcc_ip}:{fcc_port}`
- M3U 中的单播地址使用 `unicast_url`（简化版，长期有效），不包含登录 token 等动态参数

---

## 五、后端 API 设计

### 5.1 直播配置

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/live/config` | 获取所有 `live_config` |
| `PUT` | `/api/live/config` | 批量更新配置 |

### 5.2 频道同步

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/live/sync` | 触发服务器频道同步 |

返回：`{ "status": "success", "count": 120, "disabled": 3, "message": "同步完成，新增120个频道，3个下线频道已禁用" }`

### 5.3 频道列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/live/channels` | 分页查询频道列表 |

参数：`?category_id=1&enabled=1&page=1&limit=50`

### 5.4 频道 CRUD

| 方法 | 路径 | 说明 |
|------|------|------|
| `PUT` | `/api/live/channels/{id}` | 更新频道（分类、排序、启用、name、tvg_id、logo 等） |
| `DELETE` | `/api/live/channels/{id}` | 删除频道（仅 `source='external'`） |
| `POST` | `/api/live/channels` | 手动添加外部频道 |

### 5.5 分类管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/live/categories` | 获取所有分类 |
| `POST` | `/api/live/categories` | 新增分类 |
| `PUT` | `/api/live/categories/{id}` | 更新分类 |
| `DELETE` | `/api/live/categories/{id}` | 删除分类 |

### 5.6 外部频道导入

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/live/import` | 导入外部频道（文件上传或粘贴内容） |

请求格式：
- `multipart/form-data`: 上传 M3U/CSV 文件
- `application/json`: `{ "format": "m3u", "content": "#EXTM3U\n..." }`

返回：`{ "new": 5, "skipped": 3, "total": 8 }`

### 5.7 M3U 生成

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/live/tv.m3u` | 生成完整 M3U 文件 |

参数：`?category_id=1`（可选，按分类）、`?source=server`（可选，来源过滤）

返回：`Content-Type: application/vnd.apple.mpegurl`

### 5.8 排序更新

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/live/channels/reorder` | 批量更新频道排序 |

Body：`{ "order": [ {"id": 1, "sort_index": 0}, {"id": 2, "sort_index": 1}, ... ] }`

---

## 六、Web UI 设计

### 6.1 侧边栏导航

```
⚙️ 系统凭证配置
🔄 数据同步管理
📺 直播频道管理  ← 新增
📋 系统日志
```

### 6.2 直播频道管理页面布局

```
┌──────────────────────────────────────────────────────────────────┐
│ 直播频道管理                                    [🔄 同步频道] [⚙️ 设置]│
├──────────────────────────────────────────────────────────────────┤
│ 分类: [全部 ▾] | 显示: [✓]启用 | udpxy: http://192.168.1.1:6688  │
├──────────────────────────────────────────────────────────────────┤
│ ☑  │ # │ 频道名称       │ ID   │ 分类      │ 组播地址      │ 操作  │
│────│───│────────────────│──────│───────────│──────────────│───────│
│ ✓  │ 1 │ CCTV1综合高清  │4646  │ 央视高清  │233.50.201... │[编辑] │
│ ✓  │ 2 │ 浙江卫视高清   │3844  │ 卫视高清  │233.50.201... │[编辑] │
│ ...（SortableJS 拖拽排序）                                      │
├──────────────────────────────────────────────────────────────────┤
│ 统计：服务器 120 | 外部 15 | 已启用 130 | 已禁用 5                │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ 外部频道管理                                    [📥 导入]          │
│ 导入方式: [粘贴链接] | [上传文件]  格式: [M3U ▾]                  │
│ [_____________________________粘贴/文件内容______________________] │
│                                              [开始导入]           │
├──────────────────────────────────────────────────────────────────┤
│ ☑  │ 名称      │ ID   │ 类别   │ 组播地址     │ 操作             │
│ ✓  │ 某外部台  │ 9999 │ 其他   │ 239.x.x.x   │[启用][编辑][删除]│
│ ── │ 某下线台  │ 8888 │ 地方台 │ 233.x.x.x   │[启用][编辑][删除]│
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ M3U 输出                                      [📥 下载] [📋 复制]│
│ 地址：http://192.168.1.3:8880/api/live/tv.m3u                    │
└──────────────────────────────────────────────────────────────────┘
```

**功能说明**：
- **服务器频道列表**：显示 `source='server'` 的频道，支持 SortableJS 拖拽排序（更新 `sort_index`）
- **每行开关**：控制是否生成到 M3U（`is_enabled`）
- **编辑按钮**：弹窗修改名称、tvg-id、tvg-name、分类、logo_url
- **外部频道列表**：独立区域显示 `source='external'` 的频道，支持启用/禁用/编辑/删除
- **导入频道**：支持粘贴 M3U 内容或上传文件，自动解析去重
- **M3U 区域**：显示访问地址，提供下载和复制按钮

### 6.3 直播设置弹窗

```
┌──────────────────────────────────────────────┐
│ 直播频道设置                                  │
├──────────────────────────────────────────────┤
│ udpxy 地址: [http://192.168.1.1:6688       ] │
│ EPG URL:    [http://192.168.1.3:6688/...   ] │
│ LOGO 基础URL: [/static/logo/              ] │
│                                              │
│ [✓] 全局开启 FCC                              │
│ [✓] 全局开启时移 (catchup)                    │
│ [✓] 组播+单播双线模式                         │
│                                [保存] [取消] │
└──────────────────────────────────────────────┘
```

- `全局开启 FCC` → `live_config.fcc_global_enabled`
- `全局开启时移` → `live_config.timeshift_enabled`（总开关，关闭则所有频道不加 catchup）
- `组播+单播双线模式` → `live_config.m3u_dual_line`

---

## 七、LOGO 管理

LOGO 文件存放于 `static/logo/` 目录（已挂载进 Docker 镜像），通过 FastAPI 的 `/static/logo/` 路径直接访问。

- 预置 LOGO 来源：https://github.com/LionixQ/Zhejiang_Telecom_IPTV/tree/main/Logo
- 命名规则：按频道名称命名（如 `CCTV1.png`、`浙江卫视.png`）
- `live_channels.logo_url` 只存文件名，完整 URL 由 `logo_base_url` + `logo_url` 拼接

---

## 八、代码迁移：`get_channel_list`

### 8.1 迁移位置

从 `run_simulator.py` 中的旧版方法迁移到 `src/auth/simulator.py` 的 `STBSimulator` 类中。

### 8.2 原有代码（旧版）

旧版 `get_channel_list` 只解析了有限字段：
- `channel_id`、`name`、`user_channel_id`、`multicast_url`、`unicast_url`、`raw_url`

### 8.3 新版要求

新版需解析**所有字段**并返回完整字典：

| 返回值 key | 来源字段 | 说明 |
|------------|----------|------|
| `channel_id` | `ChannelID` | |
| `user_channel_id` | `UserChannelID` | |
| `name` | `ChannelName` | |
| `multicast_url` | `ChannelURL` 中 `igmp://` 部分 | |
| `unicast_url` | `ChannelURL` 中 `rtsp://` 部分（去掉 `?` 后参数） | 简化版，长期有效 |
| `unicast_url_full` | `ChannelURL` 中 `rtsp://` 部分（完整） | 含 token，每次登录变 |
| `timeshift_enabled` | `TimeShift` | `"1"` → 1 |
| `timeshift_length` | `TimeShiftLength` | int |
| `timeshift_url` | `TimeShiftURL` | |
| `is_hd` | `IsHDChannel` | int |
| `channel_type` | `ChannelType` | |
| `channel_sdp` | `ChannelSDP` | 原始值 |
| `channel_url_raw` | `ChannelURL` | 原始值（`\|` 分隔）|
| `channel_locked` | `ChannelLocked` | int |
| `preview_enabled` | `PreviewEnable` | int |
| `fcc_enabled` | `FCCEnable` | int |
| `fcc_ip` | `ChannelFCCIP` | |
| `fcc_port` | `ChannelFCCPort` | |
| `fec_port` | `ChannelFECPort` | |
| `raw_fields_json` | 全部原始键值对序列化为 JSON 字符串 | 全量冗余兜底 |

迁移要求：
- 代码精简，不包含测试代码（如写文件等）
- 去掉旧版中对 `self.state.channels` 的赋值（频道数据改由数据库管理）
- 保留原有日志输出风格

---

## 九、外部频道导入格式

### 9.1 CSV 格式（参考 LionixQ）

```csv
频道名称,组播地址,ID,视频信息,音频信息,分类
CCTV-1综合,igmp://233.50.201.118:5140,4646,1920x1080 / H264 / 25fps,MP2 / 立体声,央视
```

解析映射：
- `频道名称` → `name` / `tvg_id` / `tvg_name`
- `组播地址` → `multicast_url`
- `ID` → `channel_id`（用于去重匹配）
- `分类` → 匹配 `live_categories.name`，不存在则归入"其他"

### 9.2 M3U 格式

```m3u
#EXTINF:-1 tvg-id="CCTV1" tvg-name="CCTV1综合高清" tvg-logo="CCTV1.png" group-title="央视高清",CCTV1综合高清
igmp://233.50.201.118:5140
```

解析映射：
- `tvg-id` → `tvg_id`
- `tvg-name` → `tvg_name`
- `tvg-logo` → `logo_url`（只取文件名部分）
- `group-title` → 匹配分类
- `,` 后的内容 → `name`
- URL 行 → `multicast_url`

### 9.3 去重逻辑

导入时检查：
1. `channel_id` 相同 → 已存在，跳过
2. `multicast_url` 相同 → 已存在，跳过
3. 如果 `channel_id` 为空（某些 M3U 没有 ID），仅用 `multicast_url` 检查

---

## 十、实施计划

### Phase 1：数据层 + 同步核心
- [ ] `live_categories`、`live_channels`、`live_config` 建表（`init_db` 中追加）
- [ ] 预置分类数据 + 默认配置
- [ ] `get_channel_list` 全字段版迁移到 `STBSimulator`
- [ ] `POST /api/live/sync` 同步 API

### Phase 2：频道管理 API
- [ ] `GET /api/live/channels` 列表查询（分页、筛选）
- [ ] `PUT/DELETE/POST /api/live/channels/{id}` CRUD
- [ ] `GET/POST/PUT/DELETE /api/live/categories` 分类管理
- [ ] `POST /api/live/import` 外部频道导入
- [ ] `GET/PUT /api/live/config` 配置管理

### Phase 3：M3U 生成
- [ ] `GET /api/live/tv.m3u` M3U 生成
- [ ] udpxy 地址转换、FCC 参数、catchup 参数
- [ ] 双线模式（组播+单播）

### Phase 4：Web UI
- [ ] 侧边栏新增"直播频道管理"
- [ ] 频道列表表格 + SortableJS 拖拽排序
- [ ] 分类筛选、启用/禁用
- [ ] 编辑弹窗、删除确认
- [ ] 外部频道导入 UI（粘贴 + 文件上传）
- [ ] 设置弹窗
- [ ] M3U 下载/复制

### Phase 5：LOGO 资源
- [ ] 下载 LionixQ Logo 到 `static/logo/`
- [ ] 配置默认 `logo_base_url`

---

## 十一、待讨论 / 后续迭代

1. **EPG 抓取模块**：当前用外部 EPG XML（`x-tvg-url`），后续开发自有 EPG 抓取
2. **`TimeShiftLength=14400` 精确含义**：服务器返回 14400，实际支持 7 天回看，单位待实测确认
3. **频道自动分类匹配**：当前手动维护分类，后续可考虑根据 `ChannelName` 关键词自动归类
4. **频道状态在线检测**：批量检测组播/单播可用性（非当前优先级）

---

## 附录 A：服务器下发数据样例

```
ChannelID="3844",ChannelName="浙江卫视高清",UserChannelID="2",
ChannelURL="igmp://233.50.201.100:5140|rtsp://218.71.128.109/PLTV/88888913/224/3221228012/...smil?...",
ChannelSDP="igmp://233.50.201.100:5140|rtsp://218.71.128.109/PLTV/88888913/224/3221228012/...smil?...",
TimeShift="1",TimeShiftLength="14400",
TimeShiftURL="rtsp://218.71.128.109/PLTV/88888913/224/3221228012/...smil?...",
ChannelType="1",IsHDChannel="2",PreviewEnable="0",ChannelPurchased="1",
ChannelLocked="0",ChannelLogURL="",
PositionX="10",PositionY="10",BeginTime="5",Interval="360",Lasting="360",ActionType="1",
FCCEnable="2",ChannelFCCIP="218.71.128.109",ChannelFCCPort="8027",ChannelFECPort="0"
```

## 附录 B：M3U 时移格式模板

支持时移的频道：
```m3u
#EXTINF:-1 tvg-id="CCTV1综合高清" tvg-name="CCTV1综合高清" tvg-logo="http://xxx/Logo/CCTV1.png" group-title="央视高清" catchup="default" catchup-source="rtsp://218.71.128.109/PLTV/.../...smil?playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}",CCTV1综合高清
http://192.168.1.1:6688/udp/233.50.201.118:5140?fcc=218.71.128.109:8027
```

不支持时移的频道（无 catchup 参数）：
```m3u
#EXTINF:-1 tvg-id="CCTV3综艺高清" tvg-name="CCTV3综艺高清" tvg-logo="http://xxx/Logo/CCTV3.png" group-title="央视高清",CCTV3综艺高清
http://192.168.1.1:6688/udp/233.50.201.196:5140?fcc=218.71.128.109:8027
```
