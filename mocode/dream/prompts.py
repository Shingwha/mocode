"""Dream prompt templates — XML format."""

from ..prompt import xml_tag

DREAM_SYSTEM_PROMPT = "\n\n".join([
    xml_tag("identity",
        "You are a memory consolidation assistant. Analyze conversation summaries "
        "and current memory files, then decide whether memory updates are needed."),
    xml_tag("memory-files", "\n".join([
        "- SOUL.md: AI assistant identity, behavioral guidelines, style preferences",
        "- USER.md: User profile, preferences, tech stack, work habits",
        "- MEMORY.md: Long-term memory, important facts, project decisions, key context",
    ])),
    xml_tag("workflow", "\n".join([
        "1. Analyze conversation summaries for new information worth recording",
        "2. When updates needed: read first, then edit",
        "3. When no updates needed: reply explaining why, no tool calls",
    ])),
    xml_tag("tools", "You can use read, edit, and append tools on memory files."),
    xml_tag("rules", "\n".join([
        "1. Only make genuinely valuable updates",
        "2. Use edit for modifications, append for new content",
        "3. Be concise and structured",
        "4. No duplicates",
        "5. Always read before modifying",
    ])),
])


def build_dream_prompt(
    summaries: list[str],
    soul: str,
    user: str,
    memory: str,
) -> str:
    """Build dream user prompt with summaries and current memory."""
    summary_parts = [xml_tag(f"summary-{i}", s) for i, s in enumerate(summaries, 1)]
    return "\n\n".join([
        xml_tag("summaries", "\n\n".join(summary_parts)),
        xml_tag("current-memory", "\n\n".join([
            xml_tag("soul", soul),
            xml_tag("user", user),
            xml_tag("memory", memory),
        ])),
    ])
