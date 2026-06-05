"""mocode.app.cli — interactive CLI application."""

from __future__ import annotations

__all__ = [
    "CLIApp",
    "Display",
    "CLIDisplayHook",
    "Spinner",
    "Theme",
    "select",
    "multiselect",
    "confirm",
    "text_input",
    "Choice",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "CLIApp": (".app", "CLIApp"),
    "Display": (".display", "Display"),
    "CLIDisplayHook": (".hook", "CLIDisplayHook"),
    "Spinner": (".theme", "Spinner"),
    "Theme": (".theme", "Theme"),
    "Choice": (".prompts", "Choice"),
    "select": (".prompts", "select"),
    "multiselect": (".prompts", "multiselect"),
    "confirm": (".prompts", "confirm"),
    "text_input": (".prompts", "text_input"),
}


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        module_path, attr = _LAZY_IMPORTS[name]
        import importlib

        module = importlib.import_module(module_path, __name__)
        value = getattr(module, attr)
        globals()[name] = value  # cache for subsequent access
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
