# Command Code Provider 配置说明

> 生成时间：2026-10-02 12:33
> 适用环境：pi coding agent（Windows）

---

## 一、配置概览

| 项目 | 值 |
|---|---|
| Provider 名称 | `commandcode` |
| 配置文件 | `C:\Users\shifu\.pi\agent\models.json` |
| API 协议 | `openai-completions`（OpenAI Chat Completions）|
| Base URL | `https://api.commandcode.ai/provider/v1` |
| 聊天端点 | `https://api.commandcode.ai/provider/v1/chat/completions` |
| API Key | ⚠️ **尚未配置**，见第五节 |
| 已添加模型数 | 6 个 |

---

## 二、模型清单

| 显示名 | API ID | 上下文 | 多模态 | 输入价 | 输出价 | 缓存读 |
|---|---|---|---|---|---|---|
| DeepSeek V4.1 Flash | `deepseek/deepseek-v4.1-flash` | 1M | ✓ 图像 | $0.15/M | $0.60/M | $0.003/M |
| DeepSeek V4.1 Flash Fast | `deepseek/deepseek-v4.1-flash-fast` | 1M | ✓ 图像 | $0.16/M | $0.58/M | $0.02/M |
| Space Bunny Alpha | `stealth/space-bunny-alpha` | 1M | ✓ 图像 | 免费 | 免费 | 免费 |
| Ling 3.1 Flash | `inclusionai/ling-3.1-flash:free` | 262K | ⚠️ 待验证 | 免费 | 免费 | 免费 |
| MiMo V2.6 Flash | `xiaomi/mimo-v2.6-flash` | 1,048,576 | ✓ 图像 | $0.14/M | $0.28/M | $0.0028/M |
| MiMo V2.6 Pro | `xiaomi/mimo-v2.6-pro` | 1,048,576 | ✓ 图像 | $0.435/M | $0.87/M | $0.0036/M |

**模型 ID 格式要点：必须带厂商前缀。** 例如 DeepSeek 是 `deepseek/...`、Space Bunny 是 `stealth/...`、Ling 是 `inclusionai/...`、MiMo 是 `xiaomi/...`，Ling 免费版还带 `:free` 后缀。这些 ID 已通过官方 `GET /provider/v1/models` 接口逐一核实。

---

## 三、完整配置内容（models.json）

```json
{
  "providers": {
    "commandcode": {
      "baseUrl": "https://api.commandcode.ai/provider/v1",
      "api": "openai-completions",
      "apiKey": "$COMMANDCODE_API_KEY",
      "models": [
        {
          "id": "deepseek/deepseek-v4.1-flash",
          "name": "DeepSeek V4.1 Flash",
          "input": ["text", "image"],
          "contextWindow": 1000000,
          "maxTokens": 65536,
          "reasoning": true,
          "cost": { "input": 0.15, "output": 0.6, "cacheRead": 0.003, "cacheWrite": 0 }
        },
        {
          "id": "deepseek/deepseek-v4.1-flash-fast",
          "name": "DeepSeek V4.1 Flash Fast",
          "input": ["text", "image"],
          "contextWindow": 1000000,
          "maxTokens": 65536,
          "reasoning": true,
          "cost": { "input": 0.16, "output": 0.58, "cacheRead": 0.02, "cacheWrite": 0 }
        },
        {
          "id": "stealth/space-bunny-alpha",
          "name": "Space Bunny Alpha (Free)",
          "input": ["text", "image"],
          "contextWindow": 1000000,
          "maxTokens": 65536,
          "reasoning": true,
          "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 }
        },
        {
          "id": "inclusionai/ling-3.1-flash:free",
          "name": "Ling 3.1 Flash (Free)",
          "input": ["text", "image"],
          "contextWindow": 262144,
          "maxTokens": 65536,
          "reasoning": true,
          "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 }
        },
        {
          "id": "xiaomi/mimo-v2.6-flash",
          "name": "MiMo V2.6 Flash",
          "input": ["text", "image"],
          "contextWindow": 1048576,
          "maxTokens": 65536,
          "reasoning": true,
          "cost": { "input": 0.14, "output": 0.28, "cacheRead": 0.0028, "cacheWrite": 0 }
        },
        {
          "id": "xiaomi/mimo-v2.6-pro",
          "name": "MiMo V2.6 Pro",
          "input": ["text", "image"],
          "contextWindow": 1048576,
          "maxTokens": 65536,
          "reasoning": true,
          "cost": { "input": 0.435, "output": 0.87, "cacheRead": 0.0036, "cacheWrite": 0 }
        }
      ]
    }
  }
}
```

---

## 四、多模态（图像）支持情况

| 模型 | 官方描述 | 结论 |
|---|---|---|
| DeepSeek V4.1 Flash | "V4.1 hybrid-attention reasoning **with vision**" | ✓ 确认支持 |
| DeepSeek V4.1 Flash Fast | "High throughput V4.1 Flash" | ✓ 同源变体，按支持处理 |
| Space Bunny Alpha | "fast **multimodal** reasoning model" | ✓ 确认支持 |
| MiMo V2.6 Flash / Pro | "**multimodal** agentic coding" | ✓ 确认支持 |
| Ling 3.1 Flash | "hybrid-reasoning MoE for coding & tool-using agents" | ⚠️ **未提及视觉能力** |

所有模型的 `input` 均配置为 `["text", "image"]`。若 Ling 3.1 Flash 发送图片时报错，将其改为 `["text"]` 即可。

---

## 五、待办：配置 API Key

当前配置使用环境变量占位符 `$COMMANDCODE_API_KEY`，两种方式任选其一：

**方式 A：设置环境变量（推荐，key 不落盘明文）**
1. 在系统「环境变量」中添加用户变量 `COMMANDCODE_API_KEY`，值为你的 API Key
2. 重启 pi 使其生效

**方式 B：直接写入配置文件**
把 `models.json` 中的 `"apiKey": "$COMMANDCODE_API_KEY"` 替换为实际 key（明文存储，注意保密）。

> API Key 获取方式：登录 https://commandcode.ai/studio/ 后创建。
> 注意：Go 套餐不含 API 权限，GOAT / Pro / Max / Team / Provider 套餐均可。

---

## 六、使用方法

1. 配置好 API Key 后，在 pi 中输入 `/model`（会自动重载 `models.json`）
2. 搜索并选择 `commandcode` 下的目标模型
3. 若改动了 `models.json`，用 `/reload` 重载配置

---

## 七、备注与已知限制

1. **`maxTokens` 为估算值**：官方未公布各模型输出上限，统一填 65536，拿到官方数据后可调整。
2. **DeepSeek 分时定价**：V4.1 Flash 系列有峰谷价，本表采用官网展示的 off-peak（低谷）价；高峰时段（01–04、06–10 UTC 工作日）约为低谷价 2 倍。
3. **两个免费模型**：
   - Space Bunny Alpha：隐身预览期免费，随时可能结束
   - Ling 3.1 Flash：免费但每日限 300 次请求
4. **缓存写价**：官方未单独公布，统一填 0。
5. **其他可用端点**（同一套凭证）：
   - `/provider/v1/responses` — OpenAI Responses 协议
   - `/provider/v1/messages` — Anthropic Messages 协议（Claude 系列专用）
   - `/provider/v1/models` — 模型列表（无需鉴权）
   - `/provider/v1/systemone` — 决策模型 `typesafe/jev`

---

## 八、参考链接

- 端点与协议文档：https://commandcode.ai/docs/provider
- 模型总览：https://commandcode.ai/models
- 定价与限制：https://commandcode.ai/docs/resources/pricing-limits
- GOAT 套餐说明：https://commandcode.ai/docs/plans/goat
- 模型实时列表：https://api.commandcode.ai/provider/v1/models
