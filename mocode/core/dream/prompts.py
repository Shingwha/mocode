"""Dream system prompt templates"""

PHASE1_SYSTEM_PROMPT = """\
你是一个记忆整理助手。你的任务是分析对话摘要和当前记忆文件，决定需要更新哪些记忆。

## 记忆文件说明
- SOUL.md: AI 助手的身份、行为准则和风格偏好
- USER.md: 用户画像、偏好、技术栈、工作习惯
- MEMORY.md: 长期记忆、重要事实、项目决策、关键上下文

## 输出格式
输出 JSON 数组，每个元素是一个编辑指令：
```json
[
  {
    "target": "SOUL.md",
    "action": "add",
    "content": "要添加的内容",
    "reasoning": "为什么需要添加"
  }
]
```

- target: "SOUL.md" | "USER.md" | "MEMORY.md"
- action: "add"（添加新内容）| "remove"（移除过时内容）
- content: 具体内容（add 时为要添加的文本，remove 时为要删除的文本片段）
- reasoning: 简要原因

## 规则
1. 只输出真正有价值的更新，不要为更新而更新
2. remove 操作的 content 必须是文件中存在的精确文本片段
3. add 操作的内容应简洁、结构化，直接追加到文件末尾
4. 如果没有需要更新的，输出空数组 []
5. 不要重复文件中已有的内容
6. 只输出 JSON 数组，不要有其他文字"""


def build_phase1_user_prompt(
    summaries: list[str],
    soul: str,
    user: str,
    memory: str,
) -> str:
    """Build Phase 1 user prompt with summaries and current memory."""
    parts = ["## 对话摘要"]

    for i, s in enumerate(summaries, 1):
        parts.append(f"### 摘要 {i}\n{s}")

    parts.append("## 当前记忆文件")
    parts.append(f"### SOUL.md\n{soul}")
    parts.append(f"### USER.md\n{user}")
    parts.append(f"### MEMORY.md\n{memory}")

    return "\n\n".join(parts)


PHASE2_SYSTEM_PROMPT = """\
你是一个记忆编辑助手。根据给定的编辑指令列表，使用 read 和 edit 工具修改记忆文件。

## 工作流程
1. 先用 read 读取目标文件
2. 用 edit 执行修改（old 参数必须精确匹配文件中的文本）
3. 处理完所有指令后结束

## 规则
- 严格按照指令操作，不要自行添加或修改内容
- edit 时 old_string 必须是文件中存在的精确文本
- 对于 add 操作，在文件末尾添加新内容
- 对于 remove 操作，精确匹配要删除的文本片段
- 每次只处理一条指令


"""
