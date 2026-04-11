# Gateway

Gateway 将 mocode 作为聊天机器人运行在即时通讯平台上。每个用户拥有独立的会话。

## 启动

```bash
mocode gateway --type weixin    # 微信（默认）
mocode gateway --type <name>    # 其他平台
```

首次运行时，使用浏览器打开终端中提示的链接并扫码，即可连接到微信。

## 架构

```
Channel (平台适配) → MessageBus → ChannelManager → UserRouter → MocodeCore (每用户独立)
                                                        ↑
                                                  LRU 淘汰 (max_users)
```

- **Channel** — 平台适配器，接收/发送消息
- **ChannelManager** — 消息调度，含重试（指数退避，最多 3 次）
- **UserRouter** — 每用户会话池，LRU 淘汰，`asyncio.Lock` 串行化
- **MocodeCore** — 每用户独立实例，强制 yolo 模式（自动审批工具调用）

## 配置

在 `~/.mocode/config.json` 中添加：

```json
{
  "gateway": {
    "max_users": 100,
    "allow_from": ["*"],
    "cron": {
      "tick_interval_s": 1.0
    }
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `max_users` | 100 | 最大并发用户数，超出按 LRU 淘汰 |
| `allow_from` | `["*"]` | 允许的用户 ID 列表，`["*"]` 表示全部允许 |
| `cron.tick_interval_s` | 1.0 | 定时任务调度器轮询间隔（秒） |

## 微信 Gateway

### 功能

- 扫码登录（终端显示 QR 码）
- 长轮询消息接收，自动重连
- 收发图片/语音/视频/文件（CDN + AES 加解密）
- 语音转文字（需配置 Whisper 兼容 API）
- 输入状态指示（"正在输入..."）
- 长消息自动按 3500 字符拆分

### 媒体处理

- **接收**：自动下载到 `~/.mocode/media/weixin/<user_id>/`，消息内容替换为 `[image]`、`[voice]` 等占位符
- **发送**：AI 调用 `send_file` 工具排队文件，回复后自动通过 CDN 上传发送

### 状态持久化

`~/.mocode/weixin/state.json` 保存 token、轮询游标、上下文 token 等。

会话过期（`errcode == -14`）时自动暂停 60 分钟后重新登录。

## 定时任务（Cron）

Gateway 内置定时任务系统，AI 可通过 `cron` 工具管理。

### 调度模式

| 模式 | 说明 |
|------|------|
| `interval` | 固定间隔（秒） |
| `cron` | Cron 表达式（需 `croniter` 库） |
| `one_shot` | 一次性，执行后自动删除 |

### 使用

对话中直接让 AI 创建定时任务：

```
每天早上 9 点提醒我检查服务器状态
每 30 分钟检查一次构建状态
```

AI 会调用 `cron` 工具，支持 `create`、`list`、`delete`、`info` 操作。

任务数据存储在 `~/.mocode/cron/` 目录，每个任务一个 JSON 文件。

## 添加新平台

### 1. 创建通道包

```
mocode/gateway/<channel_name>/
├── __init__.py      # 导出 Channel 类
└── channel.py       # 实现 BaseChannel
```

### 2. 实现 BaseChannel

```python
from mocode.gateway.base import BaseChannel
from mocode.gateway.bus import OutboundMessage

class MyChannel(BaseChannel):
    async def start(self):
        # 连接平台，开始轮询
        ...

    async def stop(self):
        # 清理
        ...

    async def send(self, msg: OutboundMessage):
        # 发送 msg.content 和 msg.media 给 msg.chat_id
        ...
```

收到消息时调用继承的 `_handle_message()`：

```python
await self._handle_message(
    sender_id="<用户ID>",
    chat_id="<会话ID>",
    content="用户消息",
    media=["/path/to/file"],
)
```

### 3. 自动发现

`mocode gateway --type <channel_name>` 即可启动。系统通过包扫描自动发现 `BaseChannel` 子类，无需手动注册。
