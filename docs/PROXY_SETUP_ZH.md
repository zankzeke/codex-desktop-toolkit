# 国内常见代理软件：上游代理怎么填写

Codex Bridge Toolkit 的 **代理控制 → 上游代理（科学上网）** 填的是“本机代理软件提供的 HTTP 代理地址”。

推荐格式：

```text
http://127.0.0.1:端口
```

> **注意**：这里是 Toolkit 自己访问 `chatgpt.com` 时使用的“出站 HTTP 代理”，不是 Toolkit 的本地监听端口 `8787`。实际端口可以被用户修改，因此下面只列常见默认/典型值；最终请以你代理软件当前设置页显示的端口为准。

## 常见软件

| 软件 | 常见本地代理端口 | Toolkit 里建议填写 | 说明 |
|---|---:|---|---|
| Clash Verge Rev | Mixed Port `7897` | `http://127.0.0.1:7897` | Clash Verge Rev 当前默认配置常见为 `mixed-port: 7897`，Mixed 端口同时支持 HTTP/SOCKS。Toolkit 这里按 HTTP 使用。 |
| Clash for Windows / 常见 Clash 配置 | `7890` | `http://127.0.0.1:7890` | 许多 Clash/Mihomo 配置使用 `7890` 作为 HTTP 或 Mixed 端口；不同配置可能不同。 |
| Mihomo / Clash.Meta | Mixed Port 常见 `7890` | `http://127.0.0.1:7890` | 以配置文件中的 `mixed-port` 或 `port` 为准。 |
| v2rayN | HTTP 常见 `10809` | `http://127.0.0.1:10809` | `10808` 常见为 SOCKS 监听，HTTP 常见为 `10809`；新版本/自定义配置请以软件界面为准。 |
| NekoRay / NekoBox（旧版/常见配置） | HTTP 常见 `2081` | `http://127.0.0.1:2081` | 常见同时有 SOCKS `2080` 与 HTTP `2081`。不同版本可能变化。 |
| sing-box / 各类 sing-box GUI | 取决于配置 | 例如 `http://127.0.0.1:7890` | sing-box 没有统一固定端口；查看 `mixed` / `http` inbound 的 `listen_port`。 |

## 怎么确认自己的端口

### Clash Verge Rev

进入 **设置 → Clash / 端口设置**，查看 **Mixed Port（混合端口）**。若显示 `7897`：

```text
http://127.0.0.1:7897
```

### v2rayN

查看主界面/设置中的本地监听端口。如果 HTTP 代理显示 `10809`：

```text
http://127.0.0.1:10809
```

不要把仅支持 SOCKS5 的端口直接以 `http://` 填进去。

### Mihomo / Clash.Meta

查看配置文件：

```yaml
mixed-port: 7890
```

则填写：

```text
http://127.0.0.1:7890
```

## TUN 模式还需要填吗？

不一定。

如果你的 TUN 已经让 `CodexBridgeProxy.exe` 的出站请求透明走代理，**上游代理可以留空**。如果透明代理没有覆盖该进程，或者你希望明确让 Toolkit 走指定代理端口，再填写这里。

## 常见错误

### 1. 把 `8787` 填到“上游代理”

`8787` 默认是 Codex Bridge Toolkit 自己监听 Codex 的端口，不是科学上网软件端口。

### 2. 把 SOCKS 端口当成 HTTP 端口

Toolkit 当前的主代理链使用 `aiohttp` 的 HTTP proxy 支持，因此这里优先填写代理软件的 **HTTP 或 Mixed 端口**。

### 3. 地址只写 `127.0.0.1:7897`

请带协议：

```text
http://127.0.0.1:7897
```

### 4. 代理软件没启动

即使端口填对，代理软件未运行时也无法连接上游。先确认 Clash/v2rayN/Mihomo 等已经启动并且节点可用。

## 参考

端口默认值可能随着客户端版本改变。项目文档只用于帮助快速填写，**实际值永远以你的客户端当前配置为准**。
