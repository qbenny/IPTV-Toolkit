# AGENTS.md - IPTV-Toolkit 项目上下文

## 项目概述
IPTV 频道管理 / 点播聚合工具。模拟电信 IPTV 机顶盒（STB）完成开机认证、心跳保活，
并对外提供直播频道管理（M3U 订阅）、EPG 节目单（XMLTV）、TVBox 点播协议三类能力，
以 FastAPI 提供 Web UI 与 API，支持 Docker 部署。

核心理念：**本地是“代理 / 聚合层”**——所有直播、点播、EPG 数据均来自上游 IPTV 网关 / VIS 服务器，
本项目只做认证模拟、数据抓取、清洗归一化、持久化与对外暴露。

## 架构与目录结构
```
IPTV-Toolkit/
├── src/                      # ★ 主源码（唯一会被打包进镜像的代码目录）
│   ├── auth/                 # STB 机顶盒模拟器（认证 / 心跳 / 设备配置）
│   │   ├── config.py         # STBDeviceConfig：静态设备参数与请求头
│   │   ├── state.py          # STBRuntimeState：Token/Cookie/心跳/活跃时间戳
│   │   ├── simulator.py      # STBSimulator：登录握手、DES-ECB 动态算密、心跳、频道/点播拉取
│   │   └── heartbeat.py      # 心跳后台线程 + ensure_authenticated()
│   ├── db/                   # 数据库层
│   │   ├── models.py         # 建表/索引（init_db）、连接管理、旧库迁移
│   │   └── crud.py           # vod_items 增删改查 + 过滤器（m3u8池/低质量）
│   ├── api/                  # 对外 API 路由
│   │   ├── live.py           # 直播频道管理、分类、别名映射、M3U 生成
│   │   ├── epg.py            # EPG 同步触发、XMLTV 生成、节目查询
│   │   ├── tvbox.py          # TVBox 协议接口（/zjvod、/api/vod）
│   │   └── play.py           # 播放地址解析（/api/play）
│   ├── sync/                 # 数据同步层（后台线程）
│   │   ├── filter_sync.py    # VOD 全量同步（VIS filter.json → vod_items）
│   │   └── epg_sync.py       # EPG 节目单同步（VIS schedules API → epg_programs）
│   ├── web/                  # Web UI 路由（settings、STB 配置、同步状态、日志）
│   │   └── routes.py
│   └── utils/
│       ├── helpers.py        # parse_epg_json、IPTV 专网 IP 探测、LAN IP 探测
│       ├── logger.py         # 日志（文件 + 控制台），LOG_FILE = data/iptv_toolkit.log
│       └── normalize.py      # 频道名归一化（normalize_epg / normalize_logo）
├── static/                   # 前端静态文件（index.html、css/、js/；Vue3 组件化，无构建步骤）
├── data/                     # 运行时数据（SQLite / 配置 / 日志；Docker volume 挂载）
│   ├── iptv.db               # SQLite 主库
│   ├── stb_config.json       # STB 凭证（user_id/stb_id/mac_address/ip_address/base_url/des_key）
│   └── iptv_toolkit.log      # 运行日志
├── scratch/                  # 临时 / 实验目录，不打入镜像
├── tests/                    # 测试（不打入镜像）
├── tools/                    # 辅助脚本（不打入镜像）
├── sample/                   # 示例数据（不打入镜像）
├── docs/                     # 设计 / 调试 / 插件文档（不打入镜像）
└── codebuddy/                # agent/IDE 内部数据，不打入镜像
├── main.py                   # ★ 应用入口：装配各模块、挂载路由、启动认证
├── Dockerfile                # 多阶段构建（python:3.12-alpine + pycryptodome 编译）
└── requirements.txt          # 依赖：fastapi / uvicorn[standard] / requests / pycryptodome / python-multipart
```

> 注：前端已按 `static/docs/web-ui-refactoring-spec.md` 完成组件化重构（详见该文档）。

## 技术栈
- **后端**：FastAPI + Uvicorn，监听 `0.0.0.0:8880`
- **数据库**：SQLite（`data/iptv.db`，WAL 模式），`init_db()` 在启动时自动建表与迁移
- **认证算法**：`pycryptodome` 的 DES-ECB 动态算密（Authenticator 签名）
- **前端**：原生 HTML/CSS/JS（`static/`），Vue3 CDN 模式、无构建步骤；`app.js` 仅作路由壳，业务拆为 `components/` 下 Tab 组件 + `api/` 下 Service 层，样式令牌集中在 `css/tokens.css`
- **部署**：Docker 多阶段镜像，端口 8880，`data/` 通过 volume 挂载

## 核心模块说明
- **认证模拟器 (`src/auth`)**：`STBSimulator` 复刻电信机顶盒开机认证流（网关引导 →
  `authLoginHWCTC` 取 EncryptToken → DES 算密 → `ValidAuthenticationHWCTC` 取 UserToken
  → 解析 VIS VOD 地址）。`heartbeat.py` 起守护线程，按服务器下发的间隔（上限 600s）心跳保活；
  连续 3 小时无客户端请求则智能休眠释放 Token，Token 失效后自动重登录。
- **数据库 (`src/db`)**：6 张表 —— `vod_items`（点播）、`live_categories`、`live_channels`、
  `live_config`、`live_channel_aliases`（别名映射）、`epg_programs`（节目单）。
- **同步层 (`src/sync`)**：所有同步均跑在后台守护线程，通过全局 `sync_status` /
  `epg_sync_status` 暴露进度，Web 轮询查询。
- **API 层 (`src/api`)**：各模块通过 `set_simulator()` + `set_login_func()` 注入模拟器单例，
  请求时调用 `ensure_authenticated()` 保证已登录。

## 关键业务流程
1. **启动认证**：`main.py` 的 `lifespan` 读取 `data/stb_config.json` → 构建 `STBDeviceConfig`
   → `login_sim()` 登录 → 起心跳线程 → 装配所有路由。
2. **直播同步**（`POST /api/live/sync`）：经模拟器 `get_channel_list()` 拉取频道 → 解析组播 /
   单播地址 → 应用别名映射与归一化 → 写入 `live_channels`（server 源），自动禁用已下线的频道。
3. **VOD 同步**（`POST /api/sync/start` → `src/sync/filter_sync.py`）：遍历 10 个内容类型
   （电视剧/电影/综艺/动漫/少儿/纪录/新闻/体育/戏曲/其他），分页拉取 VIS `filter.json`
   → `bulk_upsert_items` 写入 `vod_items` → 清理过期条目。内置 **m3u8 池过滤**（JHT/YANHUA/YANKUM）
   与 **低质量过滤**（长标题+无评分+无集数的短视频垃圾，仅电视剧/综艺/纪录生效）。
4. **EPG 同步**（`POST /api/epg/sync` → `src/sync/epg_sync.py`）：`channelListAll` 取频道
   channelCode 映射 → 按 backTime 决定同步天数（统一过去 7 天至明天）→ 逐频道查 VIS schedules
   API → 写入 `epg_programs` → 按 `tvg_id` 去重（HD/SD 共享频道只写一次）。
5. **对外暴露**：生成 M3U（`/tv.m3u`）、XMLTV（`/epg.xml`）、TVBox 配置（`/zjvod`）与
   TVBox 协议接口（`/api/vod`），供播放器订阅。

## API 端点总览
| 路径 | 说明 |
|------|------|
| `/` | 重定向到 `/settings` |
| `/settings` | Web UI（读取 `static/index.html`） |
| `/static/*` | 静态资源 |
| `/api/live/*` | 直播频道 CRUD、分类、别名映射、配置、M3U 生成 |
| `/tv.m3u`、`/api/live/tv.m3u` | M3U 订阅（支持按分类/源过滤） |
| `/api/epg/*` | EPG 同步触发、状态、XMLTV、节目查询（分页/当前播放/统计） |
| `/epg.xml`、`/api/epg/xmltv.xml` | XMLTV 格式 EPG |
| `/zjvod` | TVBox 配置（站点、分类、过滤器） |
| `/api/vod` | TVBox 协议主入口（list/detail/wd/f 过滤） |
| `/api/play`、`/api/play.ts`、`/api/play/{id}.ts` | 点播播放地址解析 |

## 配置与数据文件
- **`data/stb_config.json`**：STB 凭证。留空 `ip_address` 时进入 `real_ip_mode`，自动探测
  IPTV 出网 IP（扫 10.x.x.x 网卡或 GET `192.168.1.1/iptv_ip.txt`）。
- **`data/iptv.db`**：SQLite，6 张表（见上）。`live_config` 存开关（udpxy、fcc、时移、
  低质量过滤、m3u8 过滤、epg_url、logo_base_url 等）。
- **`data/iptv_toolkit.log`**：运行日志，可经 `/api/logs` 查看/清空。
- **TVBox 过滤器**：`FILTER_CONFIG`（硬编码在 `src/api/tvbox.py`）定义各分类的过滤维度。

## 常用命令
- 本地运行：`python main.py`（监听 8880，启动即尝试登录）
- Docker 运行：`docker run -d -p 8880:8880 -v ./data:/app/data --name iptv-toolkit ghcr.io/qbenny/iptv-toolkit:latest`
- 初始化/测试数据库：`python -m src.db.crud`

## 注意事项 / 开发约定
- **不要直接修改程序代码**：改动前先给出修改方案，与用户确认后再动手。
- **镜像边界**：只有 `src/`、`static/`、`main.py`、`Dockerfile`、`requirements.txt` 等会被打包；
  `data/`、`old/`、`research_and_debug/`、`scratch/`、`codebuddy/`、文档均已在 `.dockerignore`
  中排除。新增不应打入镜像的目录时，务必同步更新 `.dockerignore`。
- **模拟器注入模式**：所有 API 模块依赖 `main.py` 注入的模拟器单例，新增接口需
  `set_simulator()` + `set_login_func()` 并调用 `ensure_authenticated()`。
- **后台线程同步**：同步任务不阻塞请求；进度通过 `sync_status` / `epg_sync_status` 暴露。
- **归一化**：频道显示名/EPG 匹配走 `normalize_epg`，Logo 文件名走 `normalize_logo`（保留 4K/8K）。
- **数据库路径**：`data/iptv.db`，Docker 下靠 volume 持久化；`init_db()` 含旧库列迁移，无需手动改表。
