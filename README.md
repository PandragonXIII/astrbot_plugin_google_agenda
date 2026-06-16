# astrbot_plugin_google_agenda

AstrBot 插件：通过 QQ 命令或 LLM tool 创建 Google Calendar 事件与 Google Tasks 待办事项。

当前版本通过 **SSH tunnel OAuth** 完成远程服务器授权（无需服务器浏览器），并已适配 Google Cloud Console 的 Desktop app 和 Web application 两种 OAuth client 类型。

## 功能

- 创建 Google Calendar 事件（`/gcal_event`）
- 创建 Google Tasks 待办（`/gtask_create`）
- LLM tool 自动调用（`create_google_calendar_event` / `create_google_task`）
- 远程 SSH tunnel OAuth 授权（`/gagenda_auth`）
- 授权状态查询（`/gagenda_auth_status`）

## 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `credentials_path` | string | — | OAuth client JSON 路径（Google 控制台下载） |
| `calendar_id` | string | `primary` | Google Calendar ID |
| `tasklist_id` | string | `@default` | Google Tasks list ID |
| `default_timezone` | string | `Asia/Shanghai` | 默认时区 |
| `default_event_duration_minutes` | int | 60 | 事件默认持续时间 |
| `enable_command_fallback` | bool | true | 是否启用 QQ 命令入口 |
| `auth_port` | int | 8765 | OAuth callback 监听端口 |
| `auth_timeout_seconds` | int | 300 | 授权回调超时时间 |
| `auth_ssh_target` | string | 自动探测 | SSH tunnel 目标，例如 `jing@1.2.3.4` |

token 自动保存到 AstrBot 标准插件数据目录 `data/plugin_data/astrbot_plugin_google_agenda/token.json`，无需单独配置 `token_path`。

## 命令

```text
/gagenda_status       查看插件状态与配置
/gagenda_auth         生成 SSH tunnel OAuth 授权 URL
/gagenda_auth_status  查看当前授权进度
/gcal_event {json}    创建日历事件
/gtask_create {json}  创建待办事项
```

示例：

```text
/gcal_event {"title":"开会","start":"2026-06-16T15:00:00","end":"2026-06-16T16:00:00","location":"办公室"}
/gtask_create {"title":"交 project report","due":"2026-06-19"}
```

## LLM tools

### `create_google_calendar_event`

| 参数 | 必填 | 说明 |
|---|---|---|
| `title` | ✓ | 事件标题 |
| `start` | ✓ | ISO datetime/date，如 `2026-06-16T15:00:00` |
| `end` | | ISO datetime/date，缺省时使用默认时长 |
| `description` | | 事件描述 |
| `location` | | 地点 |
| `timezone` | | IANA 时区，默认使用配置值 |
| `calendar_id` | | Calendar ID，默认 `primary` |

### `create_google_task`

| 参数 | 必填 | 说明 |
|---|---|---|
| `title` | ✓ | 待办标题 |
| `notes` | | 待办备注 |
| `due` | | RFC3339/ISO 日期或时间 |
| `tasklist_id` | | Task list ID，默认 `@default` |

## OAuth 登录流程（SSH tunnel）

完整说明见 [AUTH_SSH_TUNNEL.md](AUTH_SSH_TUNNEL.md)，简要步骤：

### 1. Google Cloud Console 准备

- 创建项目，启用 **Google Calendar API** 和 **Google Tasks API**
- 配置 OAuth consent screen
- 创建 OAuth 2.0 Client ID（Desktop app 或 Web application 均可）
- Web application 需要在控制台填写 `http://127.0.0.1:8765/` 为重定向 URI

### 2. 插件配置

```json
{
  "credentials_path": "/path/to/oauth_client.json",
  "auth_ssh_target": "jing@1.2.3.4"
}
```

### 3. QQ 执行

```text
/gagenda_auth
```

插件返回 SSH tunnel 命令和 Google 登录 URL。

### 4. 本地执行

```bash
# 本地电脑终端，保持窗口不关
ssh -L 8765:127.0.0.1:8765 jing@1.2.3.4
```

然后在本地浏览器打开 Google 登录 URL。

### 5. 检查结果

```text
/gagenda_auth_status
```

成功后可测试：

```text
/gcal_event {"title":"测试事件","start":"2026-06-16T15:00:00"}
```

## 服务器代理

如果 AstrBot 服务器直连 Google 不通，需要配置 SOCKS5 代理。推荐 systemd override：

```ini
[Service]
Environment="ALL_PROXY=socks5h://127.0.0.1:7897"
```

并确保 Python 环境安装了 `PySocks`（已包含在 `requirements.txt`）。

验证代理：

```bash
curl --socks5 127.0.0.1:7897 https://oauth2.googleapis.com/token -I
# 返回 HTTP/2 404 即为可达
```

## 依赖

```text
google-api-python-client
google-auth-httplib2
google-auth-oauthlib
PySocks
```

安装到 AstrBot 的 Python 环境。

## 注意事项

- 使用 OAuth user credentials（非 service account），适合同步个人 Google Calendar/Tasks
- `calendar_id: "primary"` 对应 Google 账号的主日历
- `tasklist_id: "@default"` 对应默认 Tasks 列表
- token 过期后插件会自动刷新
- 不要将 `token.json` 和 `oauth_client.json` 上传到公开仓库
