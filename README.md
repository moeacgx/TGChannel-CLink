# Telegram频道消息处理机器人

一个智能的Telegram机器人，用于监听指定频道的消息并自动处理包含特定文本的消息，支持链接替换和交互式管理。

## ✨ 功能特性

### 🔍 核心功能
- **智能消息监听**: 监听配置的频道消息更新
- **文本检测**: 自动检测包含指定文本的消息
- **链接处理**: 智能替换消息中的Telegram链接频道ID
- **格式保持**: 完美保留原始消息的格式和样式
- **消息管理**: 自动删除原消息并发送处理后的新消息

### 🛠️ 交互式管理
- **频道管理**: 通过命令直接添加/删除监听频道
- **文本配置**: 动态修改检测文本和链接文本
- **实时状态**: 查看机器人运行状态和处理统计
- **权限控制**: 完整的管理员权限验证机制

### 📊 监控与日志
- **结构化日志**: 完整的操作日志记录
- **错误处理**: 智能错误处理和重试机制
- **统计信息**: 详细的处理统计和性能监控。

## 🚀 快速开始

### 环境要求
- Python 3.8+
- Telegram Bot Token

### 安装步骤

1. **克隆项目**
```bash
git clone <repository-url>
cd telegram-bot
```

2. **安装依赖**
```bash
pip install -r requirements.txt
```

3. **配置环境变量**
```bash
cp .env.example .env
# 编辑 .env 文件，填入你的配置
```

4. **配置频道列表**
```bash
# 编辑 channels.txt 文件，添加要监听的频道
echo "-1001234567890" >> channels.txt
echo "@your_channel" >> channels.txt
```

5. **启动机器人**
```bash
python main.py
```

## ⚙️ 配置说明

### 环境变量配置 (.env)
```env
# 必需配置
BOT_TOKEN=your_bot_token_here

# 管理员配置（用户ID，逗号分隔）
ADMIN_IDS=123456789,987654321

# 可选配置
LOG_LEVEL=INFO
```

### 频道配置 (channels.txt)
```txt
# 支持两种格式：
# 1. 频道ID格式
-1001234567890

# 2. 用户名格式
@channel_username
```

### 动态设置 (settings.json)
```json
{
  "detection_text": "▶️加入会员观看完整版",
  "link_text": "观看完整版",
  "created_at": "2025-08-15",
  "last_updated": "2025-08-15"
}
```

## 🤖 管理员命令

### 频道管理
- `/add_channel <频道>` - 添加监听频道
- `/remove_channel <频道>` - 移除监听频道
- `/list_channels` - 查看所有监听频道

### 文本配置
- `/set_text <文本>` - 设置检测文本
- `/set_link_text <文本>` - 设置链接文本

### 状态查询
- `/status` - 查看机器人运行状态
- `/help` - 显示帮助信息

### 使用示例
```
/add_channel -1001234567890
/add_channel @my_channel
/set_text ▶️加入会员观看完整版
/set_link_text 点击观看
/status
```

## 🔧 工作原理

1. **消息监听**: 机器人监听配置文件中指定的频道
2. **文本检测**: 检查新消息是否包含目标检测文本
3. **链接处理**: 使用正则表达式查找并替换链接中的频道ID
4. **消息替换**: 删除原始消息，发送处理后的新消息
5. **格式保持**: 保留原始消息的所有格式信息

### 链接处理逻辑
- 匹配格式: `https://t.me/c/数字/数字`
- ID转换: 自动处理-100前缀的频道ID格式
- 智能替换: 将链接中的频道ID替换为当前频道ID

## 📁 项目结构

```
telegram-bot/
├── main.py                 # 主程序入口
├── config.py              # 配置管理模块
├── message_handler.py     # 消息处理核心逻辑
├── link_processor.py      # 链接处理模块
├── admin_commands.py      # 管理员命令处理模块
├── logger_config.py       # 日志配置模块
├── requirements.txt       # Python依赖
├── channels.txt          # 频道列表配置
├── settings.json         # 动态设置配置
├── .env.example          # 环境变量示例
├── README.md             # 项目说明文档
└── tests/                # 测试文件目录
    ├── test_config.py
    ├── test_message_handler.py
    ├── test_link_processor.py
    └── test_admin_commands.py
```

## 🧪 测试

运行单元测试：
```bash
# 运行所有测试
pytest

# 运行特定测试文件
pytest tests/test_config.py

# 运行测试并显示覆盖率
pytest --cov=.
```

## 📝 日志

机器人会在 `logs/` 目录下生成日志文件：
- 文件名格式: `bot_YYYYMMDD.log`
- 同时输出到控制台和文件
- 支持结构化JSON格式日志

## ⚠️ 注意事项

1. **权限要求**: 机器人需要在目标频道具有删除和发送消息的权限
2. **频道ID格式**: 注意-100前缀的处理，机器人会自动转换
3. **API限制**: 注意Telegram API的频率限制
4. **备份配置**: 建议定期备份配置文件

## 🔒 安全建议

- 妥善保管BOT_TOKEN，不要泄露给他人
- 仅将管理员权限授予可信用户
- 定期检查监听频道列表
- 监控机器人的运行日志

## 🐛 故障排除

### 常见问题

**Q: 机器人无法启动**
A: 检查BOT_TOKEN是否正确设置，网络连接是否正常

**Q: 无法处理消息**
A: 确认机器人在目标频道有足够权限，检查频道ID格式

**Q: 链接替换不生效**
A: 检查消息是否包含检测文本，确认链接格式正确

**Q: 管理员命令无响应**
A: 确认用户ID在ADMIN_IDS中，检查命令格式

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情

## 🤝 贡献

欢迎提交Issue和Pull Request来改进这个项目！

## 📞 支持

如有问题或建议，请通过以下方式联系：
- 提交GitHub Issue
- 发送邮件至项目维护者

---

**⭐ 如果这个项目对你有帮助，请给它一个星标！**
