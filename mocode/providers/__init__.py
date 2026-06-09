"""Built-in providers."""

__all__ = ["OpenAIProvider"]


def __getattr__(name: str):
    if name == "OpenAIProvider":
        from .openai import OpenAIProvider

        globals()["OpenAIProvider"] = OpenAIProvider
        return OpenAIProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
