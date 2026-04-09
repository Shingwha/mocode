# Skills

Skills are modular instruction sets loaded on demand to customize AI behavior for specific tasks.

## Overview

Skills allow you to:
- Provide specialized instructions for particular domains
- Load custom tools specific to a task
- Switch between different AI "personalities" or workflows

## Directory Structure

Skills are discovered from:

```
~/.mocode/skills/          # Global skills
<project>/.mocode/skills/  # Project-level skills
mocode/skills/builtin/     # Built-in skills
```

### Skill Structure

```
~/.mocode/skills/my-skill/
├── SKILL.md           # Required: YAML frontmatter + instructions
├── script.py          # Optional: tool implementations
└── requirements.txt   # Optional: dependencies
```

## SKILL.md Format

```markdown
---
name: skill-name
description: Brief description of the skill
dependencies:
  - requests>=2.0
---

# Instructions

Detailed instructions for the AI when this skill is active...
```

### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Skill identifier (used in `/skills` command) |
| `description` | Yes | Short description shown in skill list |
| `dependencies` | No | Python packages needed by `script.py` |

The body after frontmatter is the **instruction text** injected into the system prompt when the skill is active.

## Loading Skills

### Via CLI

```bash
/skills        # Interactive selection
/skills 2      # Select by number
/skills name   # Direct activation
```

Only one skill can be active at a time. Activating a new skill deactivates the current one.

### Via SDK

```python
from mocode import MocodeCore

client = MocodeCore()

# List available skills
skills = client.list_skills()

# Activate a skill
client.activate_skill("my-skill")

# Deactivate current skill
client.deactivate_skill()
```

## Built-in Skills

### mocode-plugin-dev

Development helper for creating mocode plugins. Provides instructions and templates for plugin development.

Activate with: `/skills mocode-plugin-dev`

### mocode-config

Advanced configuration helper for mocode settings.

## Custom Tools in Skills

Skills can define custom tools in `script.py`:

```python
from mocode.tools import tool

@tool("my_tool", "Description", {"arg": "string"})
def my_tool(args: dict) -> str:
    return f"Result: {args['arg']}"
```

Tools are automatically registered when the skill is loaded.

## Dependencies

If your skill requires external packages, list them in `requirements.txt` or in the SKILL.md frontmatter:

```
requests>=2.28.0
beautifulsoup4
```

Dependencies are installed in an isolated virtual environment when the skill is loaded.

## Writing Effective Skill Instructions

Skills work by adding text to the system prompt. Write clear, actionable instructions:

```
You are a Python expert. When the user asks for code:
1. Use type hints on all functions
2. Include docstrings in Google format
3. Add unit tests in a separate test_ file
4. Follow PEP 8 style guidelines
```

Tips:
- Be specific about output format
- Define edge cases to handle
- Reference tools the AI should use
- Set tone and style preferences

## Example: Code Review Skill

`~/.mocode/skills/code-review/SKILL.md`:

```markdown
---
name: code-review
description: Expert code reviewer
---

You are a senior code reviewer. When asked to review code:
1. Check for security vulnerabilities (injection, XSS, etc.)
2. Look for performance bottlenecks
3. Identify maintainability issues
4. Suggest concrete improvements with code examples
5. Rate the code 1-5 with justification

Always provide: Issues found, Recommendations, Positive aspects.
```

`~/.mocode/skills/code-review/script.py`:

```python
from mocode.tools import tool

@tool("check_common_issues", "Check for common code issues", {"code": "string"})
def check_common_issues(args: dict) -> str:
    issues = []
    code = args["code"]
    if "eval(" in code:
        issues.append("Potential code injection via eval()")
    if "password" in code.lower() and "=" in code:
        issues.append("Possible hardcoded credentials")
    return "\n".join(issues) if issues else "No common issues detected"
```

## Disabling Skills

```bash
/skills          # Select "None" or current skill to deactivate
```

Or in SDK:
```python
client.deactivate_skill()
```
