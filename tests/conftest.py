"""Shared fixtures: one config shape, one runtime, one way to read a terminal.

Nothing here touches the real ``~/.mocode`` — every runtime's home lives inside
the test's ``tmp_path``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mocode.host.config import Config, ModelEntry, ProviderEntry
from mocode.host.runtime import MoCode

#: Every escape a terminal draw can emit — colour, cursor moves, clears.
ESCAPES = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """What a terminal draw looks like as plain text."""
    return ESCAPES.sub("", text)


def make_config() -> Config:
    """A config with two providers and no reachable endpoint.

    Tests replace the conversation's provider with a double, so the values here
    only have to be well-formed — and never real.
    """
    return Config(
        active_provider="test",
        active_model="test-model",
        providers={
            "test": ProviderEntry(
                name="Test",
                api_key="sk-test",
                base_url="http://localhost",
                models={"test-model": ModelEntry(), "other-model": ModelEntry()},
            ),
            "second": ProviderEntry(
                name="Second",
                api_key="sk-other",
                base_url="http://localhost",
                models={"second-model": ModelEntry()},
            ),
        },
    )


@pytest.fixture
def mc(tmp_path: Path) -> MoCode:
    """A runtime whose sessions and plugin loading live inside the test."""
    return MoCode(config=make_config(), home=tmp_path / "home", plugin_dirs=[])


@pytest.fixture
def make_mc(tmp_path: Path):
    """A runtime factory, for tests that need a config of their own."""

    def _make(config: Config | None = None, *, plugin_dirs: list[Path] | None = None):
        return MoCode(
            config=config if config is not None else make_config(),
            home=tmp_path / "home",
            plugin_dirs=plugin_dirs if plugin_dirs is not None else [],
        )

    return _make
