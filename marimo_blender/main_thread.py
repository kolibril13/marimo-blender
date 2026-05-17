"""Run kernel-thread work on Blender's main thread.

Blender's API is not thread-safe. Marimo cells execute on the kernel
thread (see `marimo_patches`), but anything that touches `bpy` data needs
to run on the main thread or risks crashing during a viewport redraw.

A `bpy.app.timers` callback drains a job queue on every Blender event loop
tick. The kernel thread submits work via `run_on_main(...)` and blocks on a
threading.Event until the main thread executes it.
"""
from __future__ import annotations

import queue
import threading
from typing import Any, Callable

import bpy

_QUEUE: queue.Queue = queue.Queue()
_TIMER_INTERVAL = 0.001  # 1 ms — keep cell execution latency low
_TIMER_REGISTERED = False
_LOCK = threading.Lock()


def _drain_callback() -> float:
    """Drain pending jobs on the main thread; called by bpy.app.timers."""
    while True:
        try:
            fn, args, kwargs, holder, event = _QUEUE.get_nowait()
        except queue.Empty:
            break
        try:
            holder["result"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 — relayed to caller
            holder["error"] = exc
        finally:
            event.set()
    return _TIMER_INTERVAL


def ensure_registered() -> None:
    """Register the timer if it isn't already. Must be called from the main
    thread (bpy.app.timers.register requires it).
    """
    global _TIMER_REGISTERED
    with _LOCK:
        if _TIMER_REGISTERED:
            return
        bpy.app.timers.register(
            _drain_callback,
            first_interval=0.0,
            persistent=True,
        )
        _TIMER_REGISTERED = True


def unregister() -> None:
    """Unregister the timer. Safe to call from the main thread on shutdown."""
    global _TIMER_REGISTERED
    with _LOCK:
        if not _TIMER_REGISTERED:
            return
        try:
            bpy.app.timers.unregister(_drain_callback)
        except (ValueError, RuntimeError):
            pass
        _TIMER_REGISTERED = False


def run_on_main(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Synchronously run `fn(*args, **kwargs)` on Blender's main thread.

    Blocks the calling thread until the main thread finishes the call.
    Re-raises any exception raised in the callee.
    """
    if not _TIMER_REGISTERED:
        # Safety net: if the timer hasn't been registered yet, the caller is
        # using us before ensure_registered() ran on the main thread. Trying
        # to register here from a non-main thread is unsafe, so error early.
        raise RuntimeError(
            "main_thread.ensure_registered() must be called on Blender's main "
            "thread before run_on_main() is used."
        )

    event = threading.Event()
    holder: dict[str, Any] = {}
    _QUEUE.put((fn, args, kwargs, holder, event))
    event.wait()
    if "error" in holder:
        raise holder["error"]
    return holder.get("result")
