# AGENTS.md - IPTV-Toolkit 项目上下文

## 项目概述
IPTV 频道管理工具，基于 FastAPI 构建，支持 Docker 部署。

## 目录结构
```
IPTV-Toolkit/
├── src/             # 主源码（api/, auth/, db/, sync/, utils/, web/）
├── static/          # 前端静态文件（HTML/CSS/JS）
├── tests/           # 测试脚本（已加入 .dockerignore，不打入镜像）
├── tools/           # 工具脚本（数据导入等）
├── docs/            # 文档
├── sample/          # 样本数据（已加入 .dockerignore，不打入镜像）
├── data/            # 运行时数据（SQLite 数据库等，通过 volume 挂载，不打入镜像）
├── scratch/         # 临时/实验目录（已加入 .dockerignore）
├── main.py          # FastAPI 应用入口
├── run_simulator.py # 模拟器入口
├── vod-api.py       # VOD API
├── Dockerfile       # Docker 构建文件
└── requirements.txt # Python 依赖
```

## 技术栈
- **后端**: FastAPI + Uvicorn
- **数据库**: SQLite（data/iptv.db）
- **依赖**: requests, pycryptodome
- **部署**: Docker，镜像端口 8880

## 常用命令
- 跑起来：`python main.py`
- 跑模拟器：`python run_simulator.py`

## 注意事项
- `tests/`、`tools/`、`docs/`、`sample/`、`scratch/` 均不打入 Docker 镜像
- 新增目录需要关注：如果不应打入镜像，记得同步更新 `.dockerignore`
- 数据库路径：`data/iptv.db`，通过 Docker volume 挂载
- 日志文件：`data/iptv_toolkit.log`
- 程序代码不要直接改，先给修改方案
