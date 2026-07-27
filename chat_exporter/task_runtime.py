"""Small cancellable background-task runtime for the Tk application.

The old UI started ad-hoc daemon threads from many event handlers.  That made
stale results, double exports, and inconsistent button states almost
inevitable.  This module gives every long-running activity a named channel;
submitting a new task on a channel invalidates the previous one and only the
latest result is allowed back onto the Tk thread.
"""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


class TaskCancelled(RuntimeError):
    """Raised by cooperative work after its task was superseded or cancelled."""


@dataclass(frozen=True, slots=True)
class TaskContext:
    channel: str
    generation: int
    cancel_event: threading.Event

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise TaskCancelled(f"task cancelled: {self.channel}#{self.generation}")


@dataclass(slots=True)
class _TaskState:
    generation: int
    cancel_event: threading.Event
    future: Future


class UiTaskRunner:
    """Run bounded background work and marshal only current results to Tk."""

    def __init__(self, post_ui: Callable[..., None], max_workers: int = 3):
        self._post_ui = post_ui
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, max_workers),
            thread_name_prefix="chat-exporter",
        )
        self._lock = threading.RLock()
        self._states: Dict[str, _TaskState] = {}
        self._generations: Dict[str, int] = {}
        self._closed = False

    def submit(
        self,
        channel: str,
        work: Callable[[TaskContext], Any],
        on_success: Callable[[Any], None],
        on_error: Optional[Callable[[BaseException], None]] = None,
        on_finally: Optional[Callable[[], None]] = None,
    ) -> TaskContext:
        with self._lock:
            if self._closed:
                raise RuntimeError("task runner is closed")
            previous = self._states.get(channel)
            if previous:
                previous.cancel_event.set()
            generation = self._generations.get(channel, 0) + 1
            self._generations[channel] = generation
            cancel_event = threading.Event()
            context = TaskContext(channel, generation, cancel_event)
            future = self._executor.submit(work, context)
            self._states[channel] = _TaskState(generation, cancel_event, future)

        def done(completed: Future) -> None:
            with self._lock:
                current = self._states.get(channel)
                is_current = bool(
                    current
                    and current.generation == generation
                    and current.future is completed
                    and not cancel_event.is_set()
                    and not self._closed
                )
                if current and current.future is completed:
                    self._states.pop(channel, None)

            if not is_current:
                return
            try:
                result = completed.result()
            except TaskCancelled:
                return
            except BaseException as exc:  # keep worker failures away from Tk's loop
                if on_error:
                    self._post_ui(on_error, exc)
            else:
                self._post_ui(on_success, result)
            finally:
                if on_finally:
                    self._post_ui(on_finally)

        future.add_done_callback(done)
        return context

    def cancel(self, channel: str) -> None:
        with self._lock:
            state = self._states.pop(channel, None)
            if state:
                state.cancel_event.set()

    def cancel_all(self) -> None:
        with self._lock:
            states = list(self._states.values())
            self._states.clear()
        for state in states:
            state.cancel_event.set()

    def is_running(self, channel: str) -> bool:
        with self._lock:
            state = self._states.get(channel)
            return bool(state and not state.future.done())

    def shutdown(self, wait: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.cancel_all()
        self._executor.shutdown(wait=wait, cancel_futures=True)
