# ref · Agent Plugins `mcp.json` 规范（1.0.0）

> 来源：Agent Plugins Specification 1.0.0 §4.1、§6、§7.2、§9、§10 与官方
> `schemas/1.0.0/mcp.schema.json`。`mcp` 内置插件读取**插件目录下**的 `mcp.json` 时
> 必须符合本文件；项目/用户级 mocode 配置是 mocode 自有格式（见 mocode 扩展节）。

## 1. 位置与形状（标准 §6.1 / §7.2.1）

- 固定位置：插件根目录的 `mcp.json`（与 `plugin.json` 同级）。**禁止**内联进 `plugin.json`。
- 顶层必须含 `$schema` 与 `mcpServers`，**不得有其它顶层字段**。
- `mcpServers` 是 object；键为 server 名，值为 server 配置对象。空 object 合法。
- `mcp.json` 的 `$schema` **必须与 `plugin.json` 的 `$schema` 版本一致**；不一致 ⇒
  该插件的 MCP 配置整体无效（其它组件类型不受影响）。
- 缺失 `mcp.json` 不是错误。存在但不是普通文件 ⇒ 该组件类型无效，其它照常。

## 2. 官方 JSON Schema（1.0.0）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
  "title": "Agent Plugins MCP Configuration",
  "type": "object",
  "properties": {
    "$schema": {"const": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"},
    "mcpServers": {"type": "object", "additionalProperties": {"$ref": "#/$defs/server"}}
  },
  "required": ["$schema", "mcpServers"],
  "additionalProperties": false,
  "$defs": {
    "server": {"oneOf": [
      {"$ref": "#/$defs/stdioServer"},
      {"$ref": "#/$defs/streamableHttpServer"},
      {"$ref": "#/$defs/sseServer"}
    ]},
    "stdioServer": {
      "type": "object",
      "properties": {
        "type": {"const": "stdio"},
        "command": {"type": "string", "minLength": 1},
        "args": {"type": "array", "items": {"type": "string"}},
        "env": {"type": "object",
          "propertyNames": {"not": {"enum": ["PLUGIN_ROOT", "PLUGIN_DATA"]}},
          "additionalProperties": {"type": "string"}},
        "cwd": {"type": "string",
          "pattern": "^(?:\\./|\\$\\{PLUGIN_ROOT\\}(?:/|$)|\\$\\{PLUGIN_DATA\\}(?:/|$))"}
      },
      "required": ["type", "command"],
      "additionalProperties": false
    },
    "streamableHttpServer": {
      "type": "object",
      "properties": {
        "type": {"const": "streamable-http"},
        "url": {"type": "string", "minLength": 1},
        "headers": {"$ref": "#/$defs/headers"}
      },
      "required": ["type", "url"],
      "additionalProperties": false
    },
    "sseServer": {
      "type": "object",
      "properties": {
        "type": {"const": "sse"},
        "url": {"type": "string", "minLength": 1},
        "headers": {"$ref": "#/$defs/headers"}
      },
      "required": ["type", "url"],
      "additionalProperties": false
    },
    "headers": {"type": "object", "additionalProperties": {"type": "string"}}
  }
}
```

> 注意：官方 schema 关闭 `additionalProperties`。pi 的 `exposure`/`toolExposure`/`enabled`/
> `timeout`/`description` **不在标准里**。mocode 的做法：**插件目录的 `mcp.json` 严格按标准
> 解析，未知字段 report 后忽略、不拒绝整条**（宽容但不发明语义）；项目/用户级 mocode 配置
> 额外支持这些扩展键（见 §6）。

## 3. stdio 服务器字段（标准 §7.2.1）

| 字段 | 规则 |
|---|---|
| `type` | 必须 `"stdio"` |
| `command` | **单个可执行 token**，不是 shell 命令串；裸名（走平台可执行搜索）或 `./` 开头的插件相对路径；**不得做占位符展开** |
| `args` | string[]；**支持** `${PLUGIN_ROOT}` / `${PLUGIN_DATA}` 展开 |
| `env` | object of string；**支持** `${PLUGIN_ROOT}`/`${PLUGIN_DATA}` 展开；**不得**含 `PLUGIN_ROOT`/`PLUGIN_DATA` 键；overlay 到基础环境并覆盖同名项 |
| `cwd` | 省略 ⇒ 用插件根；否则 `./...`、`${PLUGIN_ROOT}[/...]`、`${PLUGIN_DATA}[/...]`；展开后必须落在对应根内 |

- 客户端**必须**给子进程提供 `PLUGIN_ROOT`（插件根的绝对路径）与 `PLUGIN_DATA`
  （客户端管理的持久可写目录，须预先创建、可写、跨插件更新保留）。
- Windows 可为 `.bat`/`.cmd` 起平台解释器，但必须保持 `command` 单 token、`args` 分开传。

## 4. HTTP 服务器字段（标准 §7.2.1）

| 字段 | 规则 |
|---|---|
| `type` | `"streamable-http"` 或 `"sse"` |
| `url` | 绝对 HTTP(S) URL；不得含 userinfo 或 fragment；**非 loopback 必须 HTTPS**；loopback（`localhost` / IP 字面量）可用 HTTP |
| `headers` | 固定头；名字大小写不敏感，同名不同大小写 ⇒ 无效 |

- **不得**对 `url` / header 名 / header 值做占位符或环境变量展开。
- headers 是可见包数据，**不是**秘密机制；插件**不得**把凭证放进 headers。
- 客户端为实现 HTTP/MCP/授权而生成的头优先于同名配置头；不得把配置头跟随重定向转发到别的 origin。
- 授权失败 = 该 server 连接失败，不是插件配置无效。
- `streamable-http` = 当前 Streamable HTTP；`sse` = 2024-11-05 的 legacy HTTP+SSE（可选支持）。

## 5. 路径包含与失败边界（标准 §4.1 / §7.2.2）

- 插件-相对路径必须 `./` 开头、解析后仍在插件根内；`${PLUGIN_DATA}` 解析后仍在 data 目录内。
- 越界 ⇒ 该 server 条目无效（跳过），其它 server/组件继续。
- 失败边界（逐条）：`mcp.json` 顶层非法 ⇒ 整个插件 MCP 失效；单条非法 ⇒ 跳过该条；
  传输不支持 ⇒ 跳过该条；连接/认证/握手失败 ⇒ 跳过该条，其它照常。
- 标准 §7.2.2：客户端只从插件根 `mcp.json` 读；JSON 损坏 / `$schema` 不支持或与 `plugin.json`
  不匹配 ⇒ 关闭该插件的 MCP；单条不满足 §7.2.1 ⇒ 跳过该条。

## 6. mocode 扩展（项目/用户级配置，非标准）

mocode 的 `mcp` 插件额外读取：
1. `plugins.mcp.servers`（config.json 内联）
2. `<cwd>/.mocode/mcp.json`
3. `<home>/mcp.json`（`~/.mocode/mcp.json`）

这些是 **mocode 自有**，允许标准之外的键与 pi 风格展开：
- `enabled`（否 ⇒ 保留不连接）、`timeout`（per-request 秒，默认 60）、
  `exposure` / `toolExposure`、`description`。
- `${VAR}` → `os.environ`（缺失 → 空串 + report；**v1 不做 `!command`**）。
- 相对 `cwd` 按该配置文件所在目录解析。
- `${PLUGIN_ROOT}`/`${PLUGIN_DATA}` 在这些文件里无意义，跳过该条并 report。

合并优先级（高→低）与工单 2.2 一致：`plugins.mcp.servers` > `<cwd>/.mocode/mcp.json` >
`<home>/mcp.json` > 各 `plugin_sources/mcp.json`（数组序）。

## 7. 与 pi 的差异（本项目取舍）

| 项目 | pi | mocode |
|---|---|---|
| 位置 | `~/.pi/agent/mcp.json`、`.pi/mcp.json` | `~/.mocode/mcp.json`、`<cwd>/.mocode/mcp.json`、插件 `mcp.json`、`plugins.mcp.servers` |
| 传输 | stdio + streamable HTTP | v1 stdio；W3 加 streamable HTTP；`sse` 跳过并 report |
| 扩展字段 | exposure 等 | 支持（mocode 自有文件；插件文件宽容忽略） |
| OAuth | 支持 | 不做（遗留） |
| `!command` | 支持 | 不做（遗留） |
| protocol era | 支持 modern+legacy | **dual-era**（`server/discover` 探测 + `initialize` 回退） |
