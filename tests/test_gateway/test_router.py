"""Gateway UserRouter tests"""

import pytest
from unittest.mock import patch

from mocode.core.config import Config
from mocode.gateway.router import UserRouter


@pytest.fixture
def base_config():
    return Config.load(data={
        "current": {"provider": "test", "model": "test-model"},
        "providers": {
            "test": {
                "name": "Test",
                "base_url": "https://api.test.com/v1",
                "api_key": "test-key",
                "models": ["test-model"],
            }
        },
    })


@pytest.fixture
def router(base_config, tmp_path):
    session_dir = tmp_path / "sessions"
    with patch("mocode.core.session.SESSIONS_DIR", session_dir):
        r = UserRouter(
            config=base_config,
            gateway_config={},
            max_users=3,
        )
        yield r


class TestUserRouter:
    def test_get_or_create(self, router):
        session = router.get_or_create("user:1")
        assert session is not None
        assert session.session_key == "user:1"

    def test_get_or_create_reuse(self, router):
        s1 = router.get_or_create("user:1")
        s2 = router.get_or_create("user:1")
        assert s1 is s2

    def test_max_users_eviction(self, router):
        # Create max_users sessions
        for i in range(3):
            router.get_or_create(f"user:{i}")

        # Creating one more should evict LRU
        router.get_or_create("user:3")
        assert len(router._sessions) == 3

    def test_session_isolation(self, router):
        s1 = router.get_or_create("user:a")
        s2 = router.get_or_create("user:b")
        assert s1 is not s2
        assert s1.core is not s2.core

    def test_yolo_mode_forced(self, router):
        session = router.get_or_create("user:x")
        assert session.core.config.current_mode == "yolo"

    @pytest.mark.asyncio
    async def test_shutdown_all(self, router):
        router.get_or_create("user:1")
        router.get_or_create("user:2")
        await router.shutdown_all()
        assert len(router._sessions) == 0

    def test_session_save_on_evict(self, router):
        s1 = router.get_or_create("user:old")
        s1.core.agent.messages = [{"role": "user", "content": "test"}]
        # Mark as unsaved
        s1.core._mark_unsaved()

        # Fill up to max and create one more to trigger eviction
        for i in range(1, 3):
            router.get_or_create(f"user:{i}")
        router.get_or_create("user:3")

        # Old session should be evicted
        assert "user:old" not in router._sessions

    def test_last_active_updated(self, router):
        s1 = router.get_or_create("user:1")
        first_active = s1.last_active
        # Accessing again should update last_active
        import time
        time.sleep(0.01)
        router.get_or_create("user:1")
        assert s1.last_active >= first_active

    def test_dream_scheduler_started_on_create(self, router):
        """Dream scheduler should be started when a session is created."""
        session = router.get_or_create("user:dream")
        assert session.core._dream_scheduler is not None

    def test_dream_scheduler_stopped_on_remove(self, router):
        """Dream scheduler should be stopped when a session is removed."""
        session = router.get_or_create("user:dream-rm")
        assert session.core._dream_scheduler is not None
        router._remove_session("user:dream-rm")
        assert session.core._dream_scheduler is None

    def test_dream_scheduler_stopped_on_evict(self, router):
        """Dream scheduler should be stopped when a session is evicted."""
        s1 = router.get_or_create("user:evict-me")
        assert s1.core._dream_scheduler is not None

        # Fill up to max and trigger eviction
        for i in range(1, 3):
            router.get_or_create(f"user:fill-{i}")
        router.get_or_create("user:new")

        # Evicted session's scheduler should be stopped
        assert s1.core._dream_scheduler is None

    def test_dream_scheduler_disabled_when_config_disabled(self):
        """Dream scheduler should not start when dream is disabled."""
        config = Config.load(data={
            "current": {"provider": "test", "model": "test-model"},
            "providers": {
                "test": {
                    "name": "Test",
                    "base_url": "https://api.test.com/v1",
                    "api_key": "test-key",
                    "models": ["test-model"],
                }
            },
            "dream": {"enabled": False},
        })
        r = UserRouter(config=config, gateway_config={}, max_users=3)
        session = r.get_or_create("user:no-dream")
        assert session.core._dream_scheduler is None
