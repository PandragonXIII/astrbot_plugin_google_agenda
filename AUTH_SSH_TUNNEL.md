# Google Agenda OAuth 登录流程：SSH Tunnel 方案

本文档记录 `astrbot_plugin_google_agenda` 在远程服务器 / 只有 CLI 访问条件下完成 Google OAuth 登录的推荐流程。

适用场景：

- AstrBot 跑在远程 Linux 服务器上；
- 服务器没有图形浏览器；
- 希望通过 QQ 命令获取 Google 登录 URL；
- 浏览器在本地电脑上打开；
- Google OAuth 回调通过 SSH tunnel 转发回 AstrBot 服务器。

## 1. 整体原理

插件启动本地 OAuth callback server：

```text
AstrBot server: 127.0.0.1:8765
```

用户在本地电脑执行 SSH 本地端口转发：

```bash
ssh -L 8765:127.0.0.1:8765 user@bot-server
```

这样本地电脑的：

```text
127.0.0.1:8765
```

会被转发到服务器上的：

```text
127.0.0.1:8765
```

Google OAuth 登录后重定向到：

```text
http://127.0.0.1:8765/?code=...
```

浏览器访问的是本地 `127.0.0.1:8765`，SSH 会把请求转发到 AstrBot 服务器，插件收到 `code` 后向 Google token endpoint 换取 token，并保存到 `token_path`。

## 2. Google Cloud Console 配置

### 2.1 OAuth Client 类型

当前插件同时支持两种 OAuth client JSON：

- Desktop app：顶层字段为 `installed`
- Web application：顶层字段为 `web`

推荐使用 Web application 时，配置如下。

### 2.2 已获授权的 JavaScript 来源

本插件不是浏览器前端直接调用 Google API，通常可以留空。

如果控制台强制要求填写，可填：

```text
http://127.0.0.1:8765
```

### 2.3 已获授权的重定向 URI

必须和插件生成的 redirect URI 完全一致：

```text
http://127.0.0.1:8765/
```

注意：

- 使用 `http://`，不是 `https://`；
- 使用 `127.0.0.1`，不是 `0.0.0.0`；
- 端口默认是 `8765`；
- 末尾 `/` 建议保留；
- 如果插件配置改了 `auth_port`，这里也要同步修改。

## 3. 插件配置

关键配置项：

```json
{
  "credentials_path": "/path/to/oauth_client.json",
  "calendar_id": "primary",
  "tasklist_id": "@default",
  "default_timezone": "Asia/Shanghai",
  "default_event_duration_minutes": 60,
  "enable_command_fallback": true,
  "auth_port": 8765,
  "auth_timeout_seconds": 300,
  "auth_ssh_target": "user@bot-server"
}
```

说明：

- `credentials_path`：Google Cloud Console 下载或手写的 OAuth client JSON；
- `auth_port`：OAuth callback 监听端口，默认 `8765`；
- `auth_timeout_seconds`：等待授权回调的超时时间；
- `auth_ssh_target`：插件返回给 QQ 用户的 SSH 目标，例如 `jing@1.2.3.4`。

如果 `auth_ssh_target` 留空，插件会尝试使用：

```text
当前运行用户@hostname
```

但这个不一定是本地电脑能直接 SSH 的地址，建议显式配置。

## 4. 服务器访问 Google 的代理配置

OAuth callback 只解决浏览器回调问题。插件收到 `code` 后，AstrBot 服务器仍然需要访问：

```text
https://oauth2.googleapis.com/token
```

创建日历事件 / 任务时也需要访问 Google API。

如果服务器直连 Google 不通，需要给 AstrBot 进程配置代理。

### 4.1 验证 SOCKS5 代理是否可用

在服务器上测试：

```bash
curl --socks5 127.0.0.1:7897 https://oauth2.googleapis.com/token -I
```

如果返回类似：

```text
HTTP/2 404
```

说明代理链路可达。这里的 `404` 是正常的，重点是没有 timeout、network unreachable 或 SSL EOF。

如果使用 HTTP 代理出现：

```text
SSL routines::unexpected eof while reading
```

通常说明代理协议或端口不对，常见原因是把 SOCKS5 端口当 HTTP 代理使用了。

### 4.2 systemd 中配置代理

假设 AstrBot 服务名为：

```text
astrbot.service
```

创建 systemd override：

```bash
sudo systemctl edit astrbot.service
```

写入：

```ini
[Service]
Environment="ALL_PROXY=socks5h://127.0.0.1:7897"
Environment="all_proxy=socks5h://127.0.0.1:7897"
Environment="HTTP_PROXY=socks5h://127.0.0.1:7897"
Environment="http_proxy=socks5h://127.0.0.1:7897"
Environment="HTTPS_PROXY=socks5h://127.0.0.1:7897"
Environment="https_proxy=socks5h://127.0.0.1:7897"
Environment="NO_PROXY=localhost,127.0.0.1,::1"
Environment="no_proxy=localhost,127.0.0.1,::1"
```

然后：

```bash
sudo systemctl daemon-reload
sudo systemctl restart astrbot.service
```

检查环境变量是否生效：

```bash
systemctl show astrbot.service -p Environment
```

### 4.3 PySocks 依赖

SOCKS 代理需要 `PySocks`：

```bash
pip install PySocks
```

如果 AstrBot 使用虚拟环境，需要使用对应环境的 pip，例如：

```bash
/path/to/astrbot/venv/bin/pip install PySocks
```

插件的 `requirements.txt` 中应包含：

```text
PySocks
```

## 5. QQ 登录流程

### Step 1：重载插件或重启 AstrBot

确保最新代码和配置生效。

### Step 2：检查插件状态

QQ 发送：

```text
/gagenda_status
```

重点检查：

```text
dependencies: ok
token_exists: False 或 True
auth_port: 8765
auth_ssh_target: user@bot-server
```

### Step 3：生成 OAuth URL

QQ 发送：

```text
/gagenda_auth
```

插件会返回两段关键信息：

1. 本地电脑执行的 SSH tunnel 命令；
2. 本地浏览器打开的 Google 授权 URL。

示例：

```text
Google OAuth over SSH tunnel

1) On YOUR LOCAL COMPUTER, run this command first and keep it open:
ssh -L 8765:127.0.0.1:8765 user@bot-server

2) Then open this Google authorization URL in your LOCAL browser:
https://accounts.google.com/o/oauth2/auth?...
```

### Step 4：本地电脑打开 SSH tunnel

在本地电脑终端执行插件返回的 SSH 命令：

```bash
ssh -L 8765:127.0.0.1:8765 user@bot-server
```

保持这个 SSH 会话不要关闭。

### Step 5：本地浏览器打开 Google 授权 URL

在本地电脑浏览器中打开 `/gagenda_auth` 返回的 URL。

授权后，Google 会跳转到：

```text
http://127.0.0.1:8765/?code=...
```

本地 SSH tunnel 会把这个请求转发给服务器上的插件。

### Step 6：检查授权结果

QQ 发送：

```text
/gagenda_auth_status
```

成功时会显示类似：

```text
Authorization ok: Google authorization succeeded. Token saved to data/plugin_data/astrbot_plugin_google_agenda/token.json
```

## 6. 创建事件测试

授权成功后，可以用 QQ 命令测试：

```text
/gcal_event {"title":"测试事件","start":"2026-06-16T15:00:00"}
```

或者通过 LLM tool 创建日历事件。

成功后会返回 Google Calendar event ID 和链接。

## 7. 常见问题

### 7.1 SSH 显示 connection refused

错误：

```text
channel 3: open failed: connect failed: Connection refused
```

含义：SSH tunnel 尝试连接服务器的 `127.0.0.1:8765`，但该端口没有监听。

排查：

```bash
ss -ltnp | grep 8765
```

如果没有 `LISTEN`，可能是：

- `/gagenda_auth` 没有执行成功；
- 授权监听已超时；
- 插件没有重载到新版本；
- `auth_port` 配置和 SSH 命令端口不一致。

### 7.2 浏览器授权后长时间转圈

如果最后报：

```text
Failed to establish a new connection: [Errno 101] 网络不可达
```

说明插件已收到 Google callback code，但服务器无法访问 Google token endpoint。

解决：给 AstrBot 进程配置可用代理。

### 7.3 SSL EOF

错误：

```text
SSL routines::unexpected eof while reading
```

常见原因：代理协议或端口不对。例如把 SOCKS5 端口当 HTTP 代理使用。

用以下命令验证：

```bash
curl --socks5 127.0.0.1:7897 https://oauth2.googleapis.com/token -I
```

如果能返回 HTTP 状态码，说明 SOCKS5 代理可用。

### 7.4 redirect_uri_mismatch

Google 报 redirect URI 不匹配时，检查 Google Console 中的重定向 URI 是否与插件完全一致：

```text
http://127.0.0.1:8765/
```

如果修改了插件的 `auth_port`，Google Console 中的端口也必须同步修改。

## 8. 相关命令汇总

```text
/gagenda_status
/gagenda_auth
/gagenda_auth_status
/gcal_event {"title":"测试事件","start":"2026-06-16T15:00:00"}
/gtask_create {"title":"测试待办","due":"2026-06-19"}
```

## 9. 当前推荐实践

- OAuth client 可使用 Web application；
- redirect URI 使用 `http://127.0.0.1:8765/`；
- 本地浏览器打开 Google 登录 URL；
- 使用 `ssh -L` 把本地 `127.0.0.1:8765` 转发到服务器；
- 服务器侧通过 SOCKS5 代理访问 Google API；
- AstrBot systemd 环境中使用 `socks5h://127.0.0.1:7897`。
