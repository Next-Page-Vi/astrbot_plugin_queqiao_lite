# astrbot_plugin_queqiao_lite

一个轻量的 AstrBot QueQiao 适配器。

插件通过 QueQiao Protocol V2 的 WebSocket 连接 Minecraft 服务端，接收服务端事件并转发到 AstrBot 统一消息源，同时提供少量 QueQiao API 命令。

## 功能

- 连接 QueQiao WebSocket 服务端
- 支持 `access_token` 鉴权
- 支持断线重连
- 支持按配置选择要推送的事件
- 支持合并连续事件通知
- 支持抵消玩家快速上下线造成的通知抖动
- 支持向多个 AstrBot 统一消息源推送通知
- 支持 `/mc` 查询在线人数或发送全局广播
- 支持 `/mctell` 向指定玩家发送私聊消息

## 指令

| 指令 | QueQiao API | 说明 |
| --- | --- | --- |
| `/mc` | `get_status` | 查询当前在线人数和最大人数 |
| `/mc <消息>` | `broadcast` | 向 Minecraft 服务器发送全局广播 |
| `/mctell <玩家ID或UUID> <消息>` | `send_private_msg` | 向指定玩家发送私聊消息 |

示例：

```text
/mc
/mc 大家晚上好
/mctell Steve 晚上好
/mctell 00000000-0000-0000-0000-000000000000 晚上好
```

`/mctell` 中的玩家 ID 按 QueQiao 的 `nickname` 处理；如果第一个参数是 UUID，则使用 QueQiao 的 `uuid` 字段。

API 是否成功以 QueQiao 返回的 `code == 200` 且 `status == "SUCCESS"` 为准。`send_private_msg` 成功响应可以确认服务端接受并执行了私聊 API；它不能证明玩家客户端已经阅读消息。

## 已实现的 QueQiao 事件

| QueQiao 事件 | `sub_type` | 类型 | 通知内容 |
| --- | --- | --- | --- |
| `PlayerJoinEvent` | `player_join` | notice | 玩家加入服务器 |
| `PlayerQuitEvent` | `player_quit` | notice | 玩家退出服务器 |
| `PlayerDeathEvent` | `player_death` | notice | 玩家死亡信息 |
| `PlayerAchievementEvent` | `player_achievement` | notice | 玩家达成成就 |
| `PlayerChatEvent` | `player_chat` | message | `昵称 [Server]: 内容` |
| `PlayerCommandEvent` | `player_command` | message | `昵称 [Server]: 命令` |

事件由 `core/event_handler.py` 使用 Pydantic 按 `sub_type` 解析。未列出的 QueQiao 事件目前不会处理。

## 已实现的 QueQiao API

### `get_status`

由 `/mc` 无参数触发。

请求：

```json
{
  "api": "get_status",
  "data": {},
  "echo": "..."
}
```

插件读取 `server_list_ping.players.online` 和 `server_list_ping.players.max`，回复类似：

```text
当前在线 3/20。
```

### `broadcast`

由 `/mc <消息>` 触发。

请求：

```json
{
  "api": "broadcast",
  "data": {
    "message": [
      {
        "text": "大家晚上好",
        "color": "white"
      }
    ]
  },
  "echo": "..."
}
```

成功后回复：

```text
已发送到服务器。
```

### `send_private_msg`

由 `/mctell <玩家ID或UUID> <消息>` 触发。

使用昵称时：

```json
{
  "api": "send_private_msg",
  "data": {
    "uuid": null,
    "nickname": "Steve",
    "message": [
      {
        "text": "晚上好",
        "color": "white"
      }
    ]
  },
  "echo": "..."
}
```

使用 UUID 时：

```json
{
  "api": "send_private_msg",
  "data": {
    "uuid": "00000000-0000-0000-0000-000000000000",
    "nickname": null,
    "message": [
      {
        "text": "晚上好",
        "color": "white"
      }
    ]
  },
  "echo": "..."
}
```

成功后回复类似：

```text
已发送给 Steve。
```

## 配置

配置由 `_conf_schema.json` 定义。

### `queqiao_server`

| 字段 | 说明 | 默认值 |
| --- | --- | --- |
| `server_name` | 服务端名称，需要和 QueQiao 配置中的 `server_name` 保持一致 | `Server` |
| `server_uri` | QueQiao WebSocket 地址 | `ws://127.0.0.1:8080/minecraft/ws` |
| `access_token` | QueQiao 鉴权 token；服务端未开启鉴权时可留空 | 空 |

### `connection_policy`

| 字段 | 说明 | 默认值 |
| --- | --- | --- |
| `max_reconnect_attempts` | 最大重连次数；`-1` 表示无限重连 | `5` |
| `reconnect_interval` | 重连间隔，单位秒 | `60` |

### `notification`

| 字段 | 说明 | 默认值 |
| --- | --- | --- |
| `umo_list` | 要推送通知的 AstrBot 统一消息源 ID，可通过 `/sid` 获取 | `[]` |
| `enabled_events` | 要启用的 QueQiao 事件 | 见配置面板 |
| `min_merge_window` | 最小合并窗口，单位秒 | `10` |
| `max_merge_window` | 最大合并窗口，单位秒 | `60` |

如果 `enabled_events` 为空，插件不会推送任何 QueQiao 事件通知。

## 通知合并

收到 QueQiao 事件后，插件不会立即发送，而是先进入通知队列。

- 队列中第一条和最后一条事件的时间跨度达到 `max_merge_window` 时，立即发送
- 距离最后一条事件达到 `min_merge_window` 时，发送当前队列
- `player_join` 和 `player_quit` 会互相抵消，用来减少快速上下线带来的重复通知

## 项目结构

```text
.
├── main.py                  # AstrBot 插件入口和指令分发
├── _conf_schema.json        # 插件配置 schema
├── metadata.yaml            # 插件元信息
└── core
    ├── api.py               # QueQiao API 请求封装
    ├── api_handler.py       # QueQiao API 响应模型和解析
    ├── event_handler.py     # QueQiao 事件模型和解析
    ├── message_manager.py   # 文本生成、通知合并和消息发送
    └── websocket.py         # QueQiao WebSocket 连接、重连和 echo 响应分流
```

## 职责边界

- `main.py`：只处理 AstrBot 指令入口和异常兜底
- `core/api.py`：构造并发送 QueQiao API 请求
- `core/api_handler.py`：解析 QueQiao API 返回
- `core/event_handler.py`：解析 QueQiao 事件
- `core/message_manager.py`：生成文本、合并通知、发送消息
- `core/websocket.py`：维护 WebSocket 连接，并按 `echo` 匹配 API 响应

## 许可证

AGPL-3.0
