"""CancellationToken tests"""

import asyncio
import threading

import pytest

from mocode.interrupt import CancellationToken, Interrupted, InterruptReason


class TestBasic:
    def test_initial_state(self):
        token = CancellationToken()
        assert not token.is_cancelled
        assert token.reason is None

    def test_cancel(self):
        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled
        assert token.reason == InterruptReason.USER

    def test_cancel_with_reason(self):
        token = CancellationToken()
        token.cancel(InterruptReason.PERMISSION_DENIED)
        assert token.reason == InterruptReason.PERMISSION_DENIED

    def test_reset(self):
        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled
        token.reset()
        assert not token.is_cancelled
        assert token.reason is None


class TestCheck:
    def test_check_raises_when_cancelled(self):
        token = CancellationToken()
        token.cancel()
        with pytest.raises(Interrupted) as exc_info:
            token.check()
        assert exc_info.value.reason == InterruptReason.USER

    def test_check_passes_when_not_cancelled(self):
        token = CancellationToken()
        token.check()  # should not raise


class TestInterruptedException:
    def test_default_reason(self):
        exc = Interrupted()
        assert exc.reason == InterruptReason.USER
        assert "USER" in str(exc)

    def test_custom_reason(self):
        exc = Interrupted(InterruptReason.PERMISSION_DENIED)
        assert exc.reason == InterruptReason.PERMISSION_DENIED


class TestCancellable:
    @pytest.mark.asyncio
    async def test_normal_completion(self):
        token = CancellationToken()
        result = await token.cancellable(asyncio.sleep(0))
        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_interrupts_coro(self):
        token = CancellationToken()

        async def long_task():
            await asyncio.sleep(10)
            return "done"

        async def cancel_soon():
            await asyncio.sleep(0.05)
            token.cancel()

        asyncio.create_task(cancel_soon())
        with pytest.raises(Interrupted):
            await token.cancellable(long_task())


class TestThreadSafety:
    def test_concurrent_toggle(self):
        token = CancellationToken()
        errors = []

        def toggle_many():
            try:
                for _ in range(1000):
                    token.cancel()
                    _ = token.is_cancelled
                    token.reset()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=toggle_many) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
