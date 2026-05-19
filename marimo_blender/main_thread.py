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
_SHUTDOWN_POLL_INTERVAL = 0.05  # run_on_main wait granularity during teardown
_TIMER_REGISTERED = False
_LOCK = threading.Lock()


def _is_shutting_down() -> bool:
    """True once the pump is torn down, or _QUEUE is cleared during Python
    module finalization. run_on_main / the timer bail out when this holds.
    """
    return not _TIMER_REGISTERED or _QUEUE is None


def drain_sync() -> None:
    """Drain pending jobs on the calling thread (main thread only).

    Used during addon shutdown to process queued work while blocking the
    main thread — the bpy timer cannot fire while we are spinning in unregister.
    """
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


def _drain_callback() -> float | None:
    """Drain pending jobs on the main thread; called by bpy.app.timers.

    Returns None to self-unregister if it somehow fires after shutdown or
    during Python module finalization.
    """
    if _is_shutting_down():
        return None
    drain_sync()
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
    drain_sync()


def run_on_main(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Synchronously run `fn(*args, **kwargs)` on Blender's main thread.

    Blocks the calling thread until the main thread finishes the call.
    Re-raises any exception raised in the callee.
    Returns None silently when called after the timer is unregistered (shutdown).
    """
    if _is_shutting_down():
        return None

    event = threading.Event()
    holder: dict[str, Any] = {}
    _QUEUE.put((fn, args, kwargs, holder, event))
    # Poll with a short timeout instead of blocking indefinitely.  This lets
    # ThreadPoolExecutor worker threads (used by execute_cell_async via
    # loop.run_in_executor) exit cleanly if the pump is torn down between our
    # enqueue and the wait, which would otherwise deadlock Python's
    # concurrent.futures._python_exit atexit join.
    while not event.wait(timeout=_SHUTDOWN_POLL_INTERVAL):
        if _is_shutting_down():
            return None
    if "error" in holder:
        raise holder["error"]
    return holder.get("result")
