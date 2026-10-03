"""Script execution for codemode — the exec wrapper and restricted builtins.

A codemode script is Python source, compiled on the fly as the body of one
async function and executed in-process against a globals dict the plugin
assembles. Top-level ``await`` and top-level ``return`` are therefore legal,
and the script reaches the outside world only through the names in that dict.

``__builtins__`` is swapped for :data:`RESTRICTED`, a whitelist of safe,
predictable builtins. That whitelist is a **stability and resource boundary,
not a security sandbox**: it keeps the DSL surface small and stops a script
from opening files or spawning threads by accident, but the model already has
a shell tool and the script shares the interpreter's memory, so nothing here
is a defence against a determined script. Do not describe it as a sandbox.

Cancellation is contractual: a turn cancelled mid-script, or a call that runs
past its deadline, unwinds the script with ``asyncio.CancelledError``. Every
layer re-raises it untouched — it must never be converted into an ordinary
script failure, or the dispatcher would report the wrong outcome.
"""

from __future__ import annotations

import builtins
import textwrap

__all__ = [
    "CODEMODE_FILENAME",
    "CodemodeError",
    "RESTRICTED",
    "_ScriptExit",
    "run_script",
    "script_error_line",
]


class CodemodeError(Exception):
    """A codemode-level failure — an empty script, an unknown tool, a store
    limit — as opposed to an error raised by the script's own code."""


class _ScriptExit(Exception):
    """Raised by the injected ``exit()`` — a normal, successful script end.

    The tool treats it as success (unlike every other exception, which makes
    the script fail); pending store writes commit, partial output is kept.
    """


#: The whitelist installed as ``__builtins__`` for the script's globals. A
#: stability/resource boundary only — see the module docstring.
_RESTRICTED_KEYS = (
    "abs", "all", "any", "bool", "dict", "enumerate", "Exception", "float",
    "format", "frozenset", "getattr", "hasattr", "int", "isinstance", "iter",
    "len", "list", "max", "min", "next", "object", "print", "range", "repr",
    "reversed", "round", "set", "sorted", "str", "sum", "tuple", "type",
    "ValueError", "KeyError", "IndexError", "TypeError", "zip", "map",
    "filter", "divmod", "pow", "chr", "ord", "bytes", "bytearray", "slice",
)


def _whitelisted_names() -> list[str]:
    """The names a script may use: the frozen safe set, every builtin
    exception class, and ``dir``.

    Exceptions are collected by rule — ``isinstance(value, type) and
    issubclass(value, Exception)`` over ``vars(builtins)`` — which is also
    the boundary: ``BaseException`` and its non-``Exception`` children
    (``KeyboardInterrupt``, ``SystemExit``, ``GeneratorExit``) fail the rule
    with no special case. A script can name what it catches (``except
    RuntimeError``) yet can never bind the class that would swallow the
    ``CancelledError`` unwinding a stopped or timed-out script.
    """
    exceptions = sorted(
        name
        for name, value in vars(builtins).items()
        if isinstance(value, type) and issubclass(value, Exception)
    )
    return [*_RESTRICTED_KEYS, *exceptions, "dir"]


RESTRICTED: dict = {name: getattr(builtins, name) for name in _whitelisted_names()}


#: The synthetic filename scripts are compiled under. Every frame the
#: script's own code occupies names it in a traceback.
CODEMODE_FILENAME = "<codemode>"

#: The wrapper is exactly one line tall — ``async def __codemode__():`` — so
#: a line number reported against the compiled source is one more than the
#: line in the script the user wrote.
_WRAPPER_LINES = 1


def script_error_line(error: BaseException) -> int | None:
    """The 1-based line of the script *error* points at, or ``None``.

    A :class:`SyntaxError` carries its position on the compiled source. Any
    exception the script raised is located by the innermost frame of its
    traceback: it names :data:`CODEMODE_FILENAME` only while the failure is
    in the script's own code — a function nested in the script qualifies, an
    error surfacing inside the tool box (``ToolCallError``) or the plugin
    (``CodemodeError``) does not, and keeps the plain error format. The
    wrapper offset (see :func:`run_script`) is subtracted either way.
    """
    if isinstance(error, SyntaxError):
        line = error.lineno
        if line is None:
            return None
        found = line
    else:
        tb = error.__traceback__
        innermost = None
        while tb is not None:
            innermost = tb
            tb = tb.tb_next
        if innermost is None:
            return None
        if innermost.tb_frame.f_code.co_filename != CODEMODE_FILENAME:
            return None
        found = innermost.tb_lineno
    line = found - _WRAPPER_LINES
    return line if line >= 1 else None


async def run_script(script: str, env: dict) -> object:
    """Compile *script* as an async function body and run it in *env*.

    *env* is the script's globals: everything the script may touch —
    injected modules, ``tools``, ``text``, the restricted builtins — lives
    there. A single dict is passed to ``exec`` on purpose, so the compiled
    function's ``__globals__`` is exactly *env*.

    Returns the function's return value (``None`` when the script has no
    top-level ``return``). Raises :class:`CodemodeError` for an empty script
    before any compilation happens; ``SyntaxError``/``NameError``/anything
    the script itself raises propagates to the caller, which decides whether
    the script failed. ``asyncio.CancelledError`` always propagates.
    """
    if not script or not script.strip():
        raise CodemodeError("script is empty")
    source = (
        "async def __codemode__():\n" + textwrap.indent(script, "    ") + "\n"
    )
    code = compile(source, CODEMODE_FILENAME, "exec")
    env["__builtins__"] = RESTRICTED
    exec(code, env)  # single dict: the function's __globals__ is env
    return await env["__codemode__"]()
