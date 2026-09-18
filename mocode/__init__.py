"""MoCode — Lean agent framework.

Two ways in, both import-light:

* **Embed it** — :class:`MoCode` is a headless runtime. It reads config, loads
  plugins, assembles the agent and hands you an event stream. Nothing is
  printed; you decide what a run looks like.
* **Build on the kernel** — :mod:`mocode.core` has the loop, the event
  contract, the tool/hook registries and the provider protocol, with no
  application dependencies at all.

::

    from mocode import MoCode

    mc = MoCode()
    async for event in mc.chat("list the tests"):
        ...
"""

from __future__ import annotations

__version__ = "0.3.0"

# Resolved on first access so that `import mocode` and `import mocode.core`
# stay cheap — pulling in the host layer costs imports the kernel alone does
# not need.
_LAZY = {"MoCode": ("mocode.host.runtime", "MoCode")}


def __getattr__(name: str):
    try:
        module_name, attr = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None

    import importlib

    value = getattr(importlib.import_module(module_name), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})


__all__ = ["MoCode", "__version__"]
