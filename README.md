# TGChannel-CLink · Telegram 频道转发机器人（Docker 支持）

一个简单可靠的 Telegram 私聊→多频道转发机器人：
- 私聊里把消息转发给机器人，机器人并发扇出到所有已配置频道
- 自动把消息中的 `https://t.me/c/<id>/<msg>` 链接替换成目标频道对应的链接
- 支持直接发送频道链接/@用户名/-100ID 快速加入转发列表

核心实现位于 `simple_relay.py`，依赖 `python-telegram-bot 20.x`。

## ✨ 功能特性

- 多频道并发分发，单条消息扇出上限可配（代码默认 8）
- 智能链接替换（`t.me/c/...` 频道 ID 重写为目标频道对应 ID）
- 交互式频道管理：/list、/add、/remove（带内联按钮选择）、/joined（识别并一键加入机器人所在频道）
- 相册/媒体组顺序处理与去重，文本/图片/视频/文件均支持
- 防重复发送（默认 6 小时 TTL），分发进度单条消息复用与节流
- 配置与持久化：`channels.json`、`discovered_channels.json`（自动创建）；兼容旧版 `channels.txt`

## 📁 主要文件

```
simple_relay.py          # 入口与核心逻辑
channel_utils.py         # 频道标识规范化与去重
link_processor.py        # 文本内 Telegram 链接重写
requirements.txt         # 依赖清单
.env.example             # 环境变量示例（复制为 .env）
Dockerfile               # 生产镜像构建
docker-compose.yml       # 本地一键启动（含持久化挂载）
channels.json            # 已配置的目标频道（运行产生）
discovered_channels.json # 已发现的机器人所在频道（运行产生）
```

## ⚙️ 环境与前置

- Python 3.11+ 或 Docker（推荐 Docker Desktop）
- Telegram Bot Token（@BotFather 获取）
- 机器人需要在目标频道具备发消息权限（视需求授予管理员）

## 🚀 快速开始

### 方式一：Docker（推荐）

1) 准备配置

```powershell
Copy-Item .env.example .env
# 编辑 .env，设置：
# BOT_TOKEN=你的BotToken
# ADMIN_IDS=12345,67890   # 可选，逗号分隔；不设置则任何人都视为管理员
# 如需网络代理/加速，可同时加入：
# HTTP_PROXY=http://127.0.0.1:7890
# HTTPS_PROXY=http://127.0.0.1:7890
# PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
```

2) 构建并启动

```powershell
docker compose up -d --build
docker compose logs -f
```

说明：`docker-compose.yml` 会将当前目录挂载到容器 `/app`，运行中生成/更新的 `channels.json`、`discovered_channels.json` 均持久化在宿主机。

网络受限时：可在 Docker Desktop → Settings → Docker Engine 配置 registry mirrors 与 DNS，再重试。

### 方式二：本地运行（Python）

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env 填写 BOT_TOKEN/ADMIN_IDS
python -u simple_relay.py
```

## 🔧 使用说明

在 Telegram 私聊中与机器人对话：

- /start：显示使用方法与命令概览（simple_relay.py:280）
- /list：查看当前转发目标列表（simple_relay.py:266）
- /add <@user|-100id|链接 ...>：批量添加目标频道，支持 t.me/c 和 @用户名（simple_relay.py:297）
- /remove <名称|@user|-100id>：移除目标频道；不带参数会弹出可视化删除按钮（simple_relay.py:313）
- /joined：识别机器人所在但未加入工作列表的频道，支持一键加入（simple_relay.py:404）

直接把频道链接/@用户名/-100ID 发给机器人，也会自动尝试解析并加入（基于 `get_chat`，保存至 `channels.json`）。把要分发的消息（文本/图片/视频/相册）转给机器人，机器人会并发分发到全部目标频道并在一条进度消息里显示结果。

## 📦 配置与数据

- 环境变量（simple_relay.py:31-35）
  - BOT_TOKEN：必填
  - ADMIN_IDS：可选，逗号分隔。不设置则默认任何人均为管理员
- 数据文件（simple_relay.py:38-41）
  - channels.json：转发目标列表（含 id/token/name/username）
  - discovered_channels.json：发现的机器人所在频道
  - channels.txt：兼容旧版的简易列表，首次运行可为空；如存在会在首次迁移至 JSON

## 🧠 实现要点（简述）

- 链接重写：`link_processor.py` 将文本中的 `t.me/c/<id>/<msg>` 改写为目标频道对应链接
- 频道标识规范化：`channel_utils.py` 统一处理 @用户名 / -100ID / t.me 链接
- 并发与去重：媒体组顺序控制、防重复缓存（默认 6 小时 TTL），并发扇出上限默认 8

## ❓常见问题

- Docker 构建拉不下基础镜像（python:3.11-slim）
  - 配置镜像与 DNS 后重试，或设置 HTTP(S)_PROXY/PIP_INDEX_URL（compose 已支持 build args）
- Windows 挂载提示拒绝访问
  - Docker Desktop → Settings → Resources → File Sharing 允许共享对应盘符/目录
- 机器人无法向频道发消息
  - 确保机器人在目标频道具备发消息权限（必要时设为管理员）

---

如果这个项目对你有帮助，欢迎 Star 支持！

