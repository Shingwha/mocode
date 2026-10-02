"""The modern tool-change subscription — ``subscriptions/listen`` (2026-07-28).

The modern wire replaced the legacy ``tools/list_changed`` notification with
a standing subscription (SEP-2575): the client opens
``subscriptions/listen`` as a long-lived request whose response *is* the
stream of typed change events. Entering the SDK's ``listen()`` context
manager sends the request and waits for the server's acknowledgment;
iterating the stream yields :class:`~mcp.shared.subscriptions.ToolsListChanged`
events — level triggers that say *what* changed, never *how*, so the answer
is always "re-list and reconcile".

One task per connection drives the stream, and the connection's teardown
ends that task, which is how the subscription stops (leaving the context
manager; a cancelled listener closes its request's stream the same way):

* an event hands the session's ``on_changed`` callback the re-sync;
* a graceful server close ends the event loop and an abrupt drop raises
  :class:`~mcp.client.subscriptions.SubscriptionLost` — neither replays, so
  both re-listen after a backoff (the specification's own guidance);
* :class:`~mcp.client.subscriptions.ListenNotSupportedError` is a pre-2026
  connection, for which the legacy notification path is the right one — it
  is raised, never retried;
* ``MCPError`` (the request refused, or the connection gone) is reported and
  raised, because retrying it here would only fight the connection task
  that is already unwinding the session.

Nothing here touches the registry: whoever answers ``on_changed``
(``McpRuntime.sync_tools``, exactly as with the legacy notification) owns
the reconciliation. The SDK is imported inside the functions so
``import mocode`` stays light.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp import Client as SdkClient

#: Seconds before the first re-listen.
BACKOFF_INITIAL = 1.0
#: The ceiling the doubling backoff approaches.
BACKOFF_MAX = 30.0


async def watch_tools(
    client: "SdkClient",
    on_changed: Callable[[], Awaitable[None]],
    *,
    report: Callable[[str], None] | None = None,
) -> None:
    """Watch one modern connection's tool-list changes until its task ends.

    *client* is an entered SDK client — the session holds it in its
    connection task, and cancelling this watch's task is the only way
    either side stops. *on_changed* re-syncs the tool set from wherever the
    caller answers it. *report*, when given, is said on a dropped stream
    and on the ``MCPError`` that ends the watch; the caller rate-limits it.

    The stream is re-opened forever — the task lives as long as the
    connection — doubling the wait from :const:`BACKOFF_INITIAL` to
    :const:`BACKOFF_MAX` between attempts and resetting to the initial value
    on every event that arrives. The two diagnoses that do not heal are
    raised: ``ListenNotSupportedError`` (a pre-2026 server — use the legacy
    notification path) and ``MCPError`` (refused, or the connection gone).
    """
    from mcp.client.subscriptions import ListenNotSupportedError, SubscriptionLost
    from mcp.shared.exceptions import MCPError

    delay = BACKOFF_INITIAL
    while True:
        try:
            # The filter is tools-only, so every event the stream admits is a
            # tool-list change; nothing else on the modern vocabularies is
            # registered for yet (resources stay a leftover of this wave).
            async with client.listen(tools_list_changed=True) as stream:
                async for _event in stream:
                    delay = BACKOFF_INITIAL  # a live stream resets the backoff
                    try:
                        await on_changed()
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        # The re-sync failed while the stream is still open:
                        # say it and keep watching — the next event retries.
                        if report is not None:
                            report(f"re-syncing tools after a change failed: {error}")
        except ListenNotSupportedError:
            raise
        except (SubscriptionLost, TimeoutError):
            # A dropped stream, or an acknowledgment that never arrived —
            # either way nothing replays: re-listen after the backoff.
            if report is not None:
                report("tool-change subscription was dropped; re-listening")
        except MCPError as error:
            if report is not None:
                report(f"tool-change subscription failed: {error}")
            raise
        await asyncio.sleep(delay)
        delay = min(delay * 2, BACKOFF_MAX)
