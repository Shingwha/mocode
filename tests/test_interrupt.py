"""InterruptToken tests"""

import threading

from mocode.core.interrupt import InterruptToken


def test_initial_state():
    token = InterruptToken()
    assert not token.is_interrupted


def test_interrupt():
    token = InterruptToken()
    token.interrupt()
    assert token.is_interrupted


def test_reset():
    token = InterruptToken()
    token.interrupt()
    assert token.is_interrupted
    token.reset()
    assert not token.is_interrupted


def test_thread_safety():
    """Concurrent access from multiple threads does not crash"""
    token = InterruptToken()
    errors = []

    def toggle_many():
        try:
            for _ in range(1000):
                token.interrupt()
                _ = token.is_interrupted
                token.reset()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=toggle_many) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
