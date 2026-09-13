# astrbot_plugin_queqiao_lite

一个轻量的 AstrBot QueQiao 适配器。

插件通过 QueQiao Protocol V2 的 WebSocket 连接 Minecraft 服务端，接收服务端事件并转发到 AstrBot 统一消息源，同时提供少量 QueQiao API 命令。

## 版本与兼容性

当前插件版本：**v1.1.2**。需要 **AstrBot >= 4.28.0、Python >= 3.12**。

本插件对接 [17TheWord/QueQiao](https://github.com/17TheWord/QueQiao)，协议核对基准为 **QueQiao v0.5.0 / QueQiaoTool v0.6.8**，参考[官方协议文档](https://queqiao-docs.pages.dev)。

- API V2 从 QueQiao v0.2.11 开始支持，事件 V2 从 v0.3.0 开始支持。
- `/mc` 在线人数查询要求服务端提供 `get_status`，该接口从 QueQiao v0.5.0 / Tool v0.6.8 引入；仅支持 V2 不代表支持此接口。
- 兼容已知旧版字符串标题和死亡参数，也支持新版递归 `Translate`。实际能力取决于服务端实现，不保证所有旧版或衍生项目兼容。
- 原版端、Velocity 不提供死亡、成就及玩家命令事件；原版端私聊接口不可用。其他服务端也可能缺少部分玩家或事件字段。
- 只支持主动连接 QueQiao 的 WebSocket Server，不提供接收反向连接的 WebSocket Server。

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

广播和状态查询先检查外层 `code == 200` 且 `status == "SUCCESS"`。私聊还必须确认 `data.target_player` 存在：母项目可能把“玩家不存在 / 离线 / 接口不可用”包装在外层成功响应中。本插件会展示内层失败原因，不将其误报为发送成功。成功响应也不表示玩家已经阅读消息。

## 已实现的 QueQiao 事件

| QueQiao 事件 | `sub_type` | 类型 | 通知内容 |
| --- | --- | --- | --- |
| `PlayerJoinEvent` | `player_join` | notice | 玩家加入服务器 |
| `PlayerQuitEvent` | `player_quit` | notice | 玩家退出服务器 |
| `PlayerDeathEvent` | `player_death` | notice | 玩家死亡信息 |
| `PlayerAchievementEvent` | `player_achievement` | notice | 玩家达成成就 |
| `PlayerChatEvent` | `player_chat` | message | `昵称 [Server]: 内容` |
| `PlayerCommandEvent` | `player_command` | message | `昵称 [Server]: 命令` |

事件由 `core/event_handler.py` 使用 Pydantic 按 `sub_type` 解析。未列出的事件跳过并记录调试日志。

成就文本优先使用正式版字段 `achievement.translation.text`，同时接受官方文档中的 `translate` 别名；两者同时存在时优先 `translation`。随后回退到旧版完整文本、标题、成就资源 key 或通用提示。`display` 缺失时仍可处理事件。死亡事件直接使用完整文本，缺失时显示死亡提示及可用翻译键。本插件不下载或管理本地翻译库。

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

未知人数显示为 `?`，不会伪装成零；状态探测失败时显示服务端的 `error` 或 `reason`。接口不支持时提示所需的 QueQiao / Tool 版本。

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

配置由 `_conf_schema.json` 定义。旧配置字段保持不变，升级无需迁移。

在母项目的 `config.yml` 中启用 `websocket_server.enable`，设置监听地址和端口，并开启需要的 `subscribe_event` 项（成就事件的服务端订阅键为 `player_advancement`）。插件的 `server_name` 必须与服务端一致；直接填写原始名称即可，插件自动编码请求头。

`server_uri` 支持 `ws://` / `wss://`；路径按服务端或反向代理实际设置填写，默认 `/minecraft/ws` 不是母项目的强制路径。`access_token` 不要填写 `Bearer` 前缀；未开启鉴权时留空。配置面板遮罩令牌，但不加密配置文件中的值。

### `queqiao_server`

| 字段 | 说明 | 默认值 |
| --- | --- | --- |
| `server_name` | 服务端名称，需要和 QueQiao 配置中的 `server_name` 保持一致 | `Server` |
| `server_uri` | QueQiao WebSocket 地址 | `ws://127.0.0.1:8080/minecraft/ws` |
| `access_token` | QueQiao 鉴权 token；服务端未开启鉴权时可留空 | 空 |

### `connection_policy`

| 字段 | 说明 | 默认值 |
| --- | --- | --- |
| `max_reconnect_attempts` | 初次连接失败后的最大重试次数；`0` 为不重试，`-1` 为无限重试 | `5` |
| `reconnect_interval` | 重连间隔，单位秒 | `60` |

普通网络断线按配置重连。收到 `1008` 策略拒绝后停止自动重试，请检查服务器名称或令牌，再通过配置保存/重载重新启动插件。重试耗尽后也需要重载。

### `notification`

| 字段 | 说明 | 默认值 |
| --- | --- | --- |
| `umo_list` | 要推送通知的 AstrBot 统一消息源 ID，可通过 `/sid` 获取 | `[]` |
| `enabled_events` | 要启用的 QueQiao 事件 | 见配置面板 |
| `min_merge_window` | 最小合并窗口，单位秒 | `10` |
| `max_merge_window` | 最大合并窗口，单位秒 | `60` |

如果 `enabled_events` 或 `umo_list` 为空，插件不会入队或发送事件通知。只包含空白正文的聊天和命令事件不会生成消息。

## 通知合并

只有已启用的事件进入队列，使用单调时钟计时，每秒检查一次：

- 距离队列中最早事件达到 `max_merge_window`，或距离最新事件达到 `min_merge_window`，发送当前队列。
- `min_merge_window = 0` 表示不额外等待，在下一次检查时发送。
- 要求 `0 <= min_merge_window < max_merge_window`；非法窗口回退到 `10/60` 秒。
- 正常调度下最多有约 1 秒的检查延迟；平台发送耗时另计，慢平台也可能延后下一次检查。
- 同一已知服务器、同一玩家的加入和退出事件互相抵消。双方都有 UUID 时按 UUID 比较；至少一方缺 UUID 时才回退到双方的有效昵称。无法确认身份时不抵消。
- 每个目标仅尝试一次；平台未匹配或发送异常会记录目标 UMO，并继续其他目标。失败消息不会自动补发。
- 停用/重载时取消后台任务、关闭连接、结束待响应请求，并丢弃尚未发送的通知。

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

## 开发验证

使用 uv 安装临时开发依赖并运行测试；`requirements.txt` 供 AstrBot 安装插件运行依赖，`requirements-dev.txt` 额外声明测试基准 AstrBot 4.28.0。

```sh
uv run --no-project --python 3.12 --with-requirements requirements-dev.txt python -m pytest
uv tool run ruff format .
uv tool run ruff check .
```

测试使用真实 AstrBot API、隔离配置和本地模拟 WebSocket，不会向真实群聊或 Minecraft 服务端发送消息。协议载荷依据 QueQiao v0.5.0 / Tool v0.6.8 和官方文档，真实服务端联调需另外执行。

## v1.1.2 更新

- 移除弃用注册装饰器，声明 AstrBot 最低版本及插件依赖。
- 修复后台任务失败后停用异常、空通知、合并超时和玩家身份误匹配。
- 兼容新版递归翻译组件、成就字段别名与缺失展示信息。
- 修复私聊外层成功但业务失败的误报，并补充状态探测失败提示。
- 编码服务器名称，统一处理策略拒绝，清理超时/取消/断线请求。
- 遮罩令牌，保留缺失值为 `None`，补充回归测试和协议兼容说明。

## 许可证

AGPL-3.0
