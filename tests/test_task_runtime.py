from __future__ import annotations

import threading
import time

from chat_exporter.task_runtime import UiTaskRunner


def test_latest_task_wins_per_channel():
    callbacks = []
    callback_event = threading.Event()

    def post_ui(callback, *args):
        callback(*args)

    runner = UiTaskRunner(post_ui, max_workers=2)

    def slow(context):
        time.sleep(0.08)
        context.check_cancelled()
        return "old"

    runner.submit("preview", slow, lambda value: callbacks.append(value))
    runner.submit(
        "preview",
        lambda _context: "new",
        lambda value: (callbacks.append(value), callback_event.set()),
    )

    assert callback_event.wait(1)
    time.sleep(0.12)
    runner.shutdown()
    assert callbacks == ["new"]


def test_cancelled_task_does_not_call_error_handler():
    errors = []
    runner = UiTaskRunner(lambda callback, *args: callback(*args), max_workers=1)

    def work(context):
        while not context.cancelled:
            time.sleep(0.01)
        context.check_cancelled()

    runner.submit("search", work, lambda _value: None, errors.append)
    runner.cancel("search")
    time.sleep(0.05)
    runner.shutdown()
    assert errors == []
