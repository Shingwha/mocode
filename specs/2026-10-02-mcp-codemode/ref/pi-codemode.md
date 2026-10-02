# ref · pi codemode 官方文档（全文摘录）

> 来源：pi `docs/codemode.md`（本地安装 `@earendil-works/pi-coding-agent` 1.0.0）。
> 这是 mocode `codemode` 插件的**语义来源**；语言从 JS 换成 Python（见 `ref/codemode-dsl.md`）。

# Codemode

The `codemode` tool lets the model write a JavaScript script that calls pi's other tools and runs non-LLM models, such as classifiers and image models. Only the script's output reaches the model, so a script can run calls in parallel and filter large results before the model sees them.

## Scripts

The tool input is raw JavaScript source, not JSON and not a markdown code fence. It runs as the body of an async function in a QuickJS sandbox, so top-level `await` and `return` work. The sandbox has no Node APIs, file system, network, or timers; scripts reach the outside world only through tools and `models`.

A script may start with an options line:

```js
// @options: {"max_output_tokens": 2000, "timeout_ms": 60000}
```

- `max_output_tokens` (default 10000) limits the output. Longer output keeps its start and end, and the full text is written to a temp file whose path is included in the result.
- `timeout_ms` is a hard deadline for the whole script. It is unset by default. Image generation can take minutes, so do not set a short deadline for scripts that generate images.

The result starts with `Script completed` or `Script failed`, the wall time, and the output. A failed script keeps its partial output, followed by `Script error:` and the error. Tool calls are real: calls made before a failure are not undone. Calls still running when the script ends are cancelled, and unawaited promises are discarded.

## Globals

| Global | Purpose |
|---|---|
| `tools.<name>(args)` | Call a tool. See [Call tools](#call-tools). |
| `text(value)` | Add a text item to the output. Strings are added as is, other values as JSON. |
| `image(value)` | Add an image to the output: a base64 `data:` URL, an `{ image_url }` object, or an image block `{ type: "image", data, mimeType }` such as those returned by MCP tools and `models.generateImages()`. Remote URLs are not supported. PNG, JPEG, GIF, and WebP are accepted. |
| `console.log(...)` | Like `text()`; `info`, `warn`, `error`, and `debug` do the same. |
| `return value` | A top-level `return` adds the value like `text()`. |
| `exit()` | End the script successfully. |
| `store(key, value)` / `load(key)` | Keep small JSON values across `codemode` calls. See [Store values](#store-values). |
| `ALL_TOOLS` | Every callable tool as `{ name, description }`, including tools the description does not list. |
| `searchTools(query, { limit?, namespace? })` | Rank callable tools by relevance (BM25, default limit 8). Resolves to `{ name, description }[]`. |
| `describeTool(name)` | Resolves to a tool's description and TypeScript declaration, or `undefined`. |
| `describeNamespace(name)` | Resolves to `{ name, description?, instructions?, tools }` for a namespace such as an MCP server, or `undefined`. |
| `models` | List and run non-LLM models. See [Models](#models). |

## Call tools

Every tool the session can call is a method of `tools`, named by its identifier: characters that are not valid in a JavaScript identifier become `_`, so the MCP tool `mcp__dev-radius__search` is `tools.mcp__dev_radius__search`. Each method takes one object with the tool's arguments.

What a call resolves to depends on the tool:

- Tools with an output schema resolve to a structured value. `bash` resolves to `{ output, truncated, full_output_path?, exit_code, wall_time_seconds }`, also for non-zero exit codes. Its `output` is not limited to the 2000 lines or 50KB the model sees: it holds up to 1 MiB, and longer output keeps its first and last 512 KiB around an omission marker, with `truncated` set and the full output in `full_output_path`.
- MCP tools resolve to their `CallToolResult`, including `isError` and `structuredContent`.
- Other tools, such as `read`, `edit`, and `write`, resolve to their text output.

A call that fails, is blocked, or gets invalid arguments rejects with an `Error` that carries the tool's error text. Use `Promise.allSettled()` to keep the results of the calls that succeed.

The `codemode` description lists tools with their TypeScript declarations, grouped by namespace (for example one MCP server). Tools with `deferred` exposure, which includes MCP tools with the default `codemode` exposure, are not listed, so the description stays the same while MCP servers connect. Listed declarations share a budget of 3000 estimated tokens (`codemode.inlineBudget` in [settings](settings.md#tools)). Scripts find the other tools with `searchTools()`, `describeTool()`, `describeNamespace()`, or by filtering `ALL_TOOLS`.

While `codemode` is active, `codemode.mode` in settings decides how the other tools are presented. With `on` (default) declared tools stay declared, and their descriptions say how to call them from scripts. With `only` they are hidden from the model and listed in the `codemode` description instead, so the model calls them through scripts.

## Store values

`store(key, value)` keeps a JSON value under a string key for later `codemode` calls; storing `undefined` deletes the key. `load(key)` returns the value, or `undefined`. Writes are kept only when the script succeeds: each successful script that stores values appends a `codemode-store` custom entry to the session, so resumed sessions keep the values and each branch sees only the values written on its path.

The store is for small state such as IDs, cursors, or summaries. One value may have at most 262144 characters of JSON and all values together at most 1048576. Do not store image data; show images with `image()` or write them to a file with a tool.

## Models

`models` reaches the model catalog and runs non-LLM models with the session's credentials: classifiers, which answer typed questions about JSON state, and image models, which generate images. Chat models are listed but cannot be run from scripts. (mocode v1 does not implement this; see `ref/codemode-dsl.md` §9.)

### Classify

```js
const jev = await models.getModelOfType("classifier", "typesafe", "jev-latest");
const results = await Promise.all(
  messages.map((message) =>
    models.classify(jev, {
      state: { message },
      questions: {
        sentiment: {
          type: "choice",
          instructions: "How does the user feel about the product?",
          criteria: { positive: "Satisfied or happy", negative: "Unhappy or frustrated", neutral: "Neither" },
        },
        urgency: {
          type: "score",
          instructions: "How urgently does this need a reply?",
          criteria: ["no reply needed", "reply this week", "reply today"],
        },
      },
    }),
  ),
);
```

### Generate images

```js
// @options: {"timeout_ms": 300000}
const painter = await models.getModelOfType("image", "openrouter", "google/gemini-2.5-flash-image");
const result = await models.generateImages(painter, {
  input: [{ type: "text", text: "A red fox in the snow, watercolor" }],
});
if (result.stopReason !== "stop") return result.errorMessage;
for (const block of result.output) {
  if (block.type === "image") image(block);
  else text(block.text);
}
```

## Limits

- A script's VM has 256 MB of memory. Running out throws `InternalError: out of memory`; filter or aggregate large data instead of accumulating it.
- A script that waits on a promise that can never settle (no tool call pending) fails immediately, since there are no timers.
- Scripts cannot start other `codemode` scripts.

---

## 映射到 mocode（速查）

| pi | mocode |
|---|---|
| 原始 JS 输入 | `{"script": "<python>"}` |
| `tools.X(args)` Promise | `await tools.X(args)` |
| `Promise.allSettled` | `asyncio.gather(..., return_exceptions=True)` |
| `text/image/console/return/exit` | 同名 |
| `store/load` | 同名（overlay，成功才提交） |
| `ALL_TOOLS/searchTools/describeTool` | `ALL_TOOLS/search_tools/describe_tool`（同步） |
| `describeNamespace` / `models` | v1 不做 |
| `// @options: {...}` | `# @options: {...}` 或 `options` 参数 |
| QuickJS 256MB | 进程内 exec + restricted builtins；无内存硬上限（遗留） |
