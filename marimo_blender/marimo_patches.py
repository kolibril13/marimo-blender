"""Monkey-patches to make marimo run its kernel in-process inside Blender.

Marimo's default EDIT-mode kernel runs in a multiprocessing.Process, which
cannot import Blender's `bpy` because the `_bpy` C extension only exists
inside the Blender executable. We patch marimo to use its existing
threading-based code path for all sessions, so cells execute in the same
process as Blender and `import bpy` works.

We keep `is_edit_mode=True` so the kernel still gets completions, stdin,
debugger, render hooks, etc. To make that safe in a thread, we also
neutralize the subprocess-only side effects (setsid, parent poller, signal
handlers, process-global fd redirection).

Trade-off: cells run in a background thread, not Blender's main thread.
Blender's API is not thread-safe — `bpy` reads tend to work, writes may
crash on complex operations.
"""
from __future__ import annotations

import os
import threading
from typing import Any

_PATCHED = False
_RUNNING_UVICORN_SERVER: Any = None


def apply() -> None:
    """Apply all marimo patches. Idempotent."""
    global _PATCHED
    if _PATCHED:
        return

    _patch_os_setsid()
    _patch_queue_manager()
    _patch_runtime_subprocess_calls()
    _patch_runtime_stream_classes()
    _patch_kernel_manager()
    _capture_uvicorn_server()
    _register_main_thread_executor()

    _PATCHED = True


def get_running_uvicorn_server() -> Any:
    """Return the most recently constructed uvicorn.Server instance, or None.

    Used by addon_setup.Server.stop() to set should_exit on the live server
    so the uvicorn run loop drains and the background thread exits.
    """
    return _RUNNING_UVICORN_SERVER


def _capture_uvicorn_server() -> None:
    """Wrap uvicorn.Server.__init__ so every construction stashes the
    instance in a module-level slot. Marimo creates its server inside
    `_server.start.start()`, beyond reach of our normal API surface.
    """
    import uvicorn

    original_init = uvicorn.Server.__init__

    def wrapped_init(self, *args, **kwargs):
        global _RUNNING_UVICORN_SERVER
        original_init(self, *args, **kwargs)
        _RUNNING_UVICORN_SERVER = self

    uvicorn.Server.__init__ = wrapped_init


def _register_main_thread_executor() -> None:
    """Register a cell executor that runs each cell's body on Blender's main
    thread (via main_thread.run_on_main). Marimo composes this on top of the
    DefaultExecutor: our `execute_cell` calls `self.base.execute_cell` under a
    main-thread bridge. This makes bpy reads + writes from cells safe.

    Marimo's runtime context lives in a threading.local; we snapshot it on
    the kernel thread and install it on the main thread for the duration of
    the cell so UI element registration, reactive wiring, etc. work.
    """
    import asyncio

    from marimo._runtime.context.types import _THREAD_LOCAL_CONTEXT
    from marimo._runtime.executor import _EXECUTOR_REGISTRY, DefaultExecutor

    from . import main_thread

    def _run_with_ctx(ctx, fn, *args, **kwargs):
        prev = getattr(_THREAD_LOCAL_CONTEXT, "runtime_context", None)
        _THREAD_LOCAL_CONTEXT.runtime_context = ctx
        try:
            return fn(*args, **kwargs)
        finally:
            _THREAD_LOCAL_CONTEXT.runtime_context = prev

    class MainThreadExecutor:
        """Satisfies the `Executor` protocol; replaces DefaultExecutor
        entirely (marimo >= 0.23 no longer composes over a base), so we
        wrap our own DefaultExecutor instance.
        """

        name = "marimo-blender-main-thread"

        def __init__(self) -> None:
            self.base = DefaultExecutor()

        def execute_cell(self, cell, glbls):
            ctx = getattr(_THREAD_LOCAL_CONTEXT, "runtime_context", None)
            return main_thread.run_on_main(
                _run_with_ctx, ctx, self.base.execute_cell, cell, glbls
            )

        async def execute_cell_async(self, cell, glbls):
            # Cells with top-level `await` aren't supported on the main thread
            # here; we try to run via the sync path for non-async cells.
            # If the cell is actually async, we run it asynchronously but
            # outside the main thread context (async code + main thread = unsafe).
            ctx = getattr(_THREAD_LOCAL_CONTEXT, "runtime_context", None)
            loop = asyncio.get_running_loop()

            # `is_coroutine` is a method, not a property — calling it is not
            # optional: the bound method object is always truthy, which would
            # send every cell down the async branch and bypass the main thread.
            if cell.is_coroutine():
                # Cell is actually async — run it async but not on main thread
                # (bpy access will be unsafe, but at least it will work)
                return await self.base.execute_cell_async(cell, glbls)
            else:
                # Cell is not async — run it sync on main thread
                return await loop.run_in_executor(
                    None,
                    main_thread.run_on_main,
                    _run_with_ctx,
                    ctx,
                    self.base.execute_cell,
                    cell,
                    glbls,
                )

    # The registry stores factories: the kernel calls the registered value
    # with no args to construct the executor instance.
    _EXECUTOR_REGISTRY.register(
        "marimo-blender-main-thread", MainThreadExecutor
    )


def _patch_os_setsid() -> None:
    """`os.setsid` only works in the main thread on a fresh process. The
    threaded kernel doesn't need a new session group, and Blender itself
    doesn't call setsid, so a global no-op is safe.
    """
    if hasattr(os, "setsid"):
        os.setsid = lambda: None  # type: ignore[assignment]


def _patch_queue_manager() -> None:
    """Force QueueManagerImpl to use threading queues + stream_queue."""
    from marimo._session.managers import queue as queue_mod

    original_init = queue_mod.QueueManagerImpl.__init__

    def patched_init(self, *, use_multiprocessing: bool) -> None:
        original_init(self, use_multiprocessing=False)

    queue_mod.QueueManagerImpl.__init__ = patched_init


def _patch_runtime_subprocess_calls() -> None:
    """Neutralize subprocess-only calls in marimo._runtime.runtime so the
    threaded kernel doesn't try to set up process group / parent poller.
    `signal.signal` is already wrapped in addon_setup.Server.start.
    """
    from marimo._runtime import runtime

    def noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    runtime.restore_signals = noop
    runtime.start_parent_poller = noop


def _patch_runtime_stream_classes() -> None:
    """Force ThreadSafeStdout/Stderr in the runtime module to never do
    process-global fd redirection (os.dup2). The runtime asks for
    `forward_os_streams=use_fd_redirect` which would be True in our setup,
    but that would steal Blender's stdout/stderr. Override the classes the
    runtime imports so they always pass forward_os_streams=False.
    """
    from marimo._messaging import streams as streams_mod
    from marimo._runtime import runtime

    class _NoFDStdout(streams_mod.ThreadSafeStdout):
        def __init__(self, stream: Any, forward_os_streams: bool = True) -> None:
            super().__init__(stream, forward_os_streams=False)

    class _NoFDStderr(streams_mod.ThreadSafeStderr):
        def __init__(self, stream: Any, forward_os_streams: bool = True) -> None:
            super().__init__(stream, forward_os_streams=False)

    runtime.ThreadSafeStdout = _NoFDStdout
    runtime.ThreadSafeStderr = _NoFDStderr


def _patch_kernel_manager() -> None:
    """Force KernelManagerImpl.start_kernel to always use the threading path."""
    from marimo._config.settings import GLOBAL_SETTINGS
    from marimo._runtime import runtime
    from marimo._session.managers import kernel as kernel_mod

    def patched_start_kernel(self) -> None:
        assert self.queue_manager.stream_queue is not None, (
            "stream_queue must exist; QueueManagerImpl must be patched first"
        )

        # Force the module autoreloader off regardless of the user's marimo
        # config. Its per-cell-run check calls os.path.realpath on every
        # entry in sys.modules, and Blender's process carries thousands of
        # modules (bpy, addons, bundled libs) — ~80ms per run, capping UI
        # updates at ~12fps. Hot-reloading modules inside Blender's shared
        # process would be unsafe anyway.
        user_config = self.config_manager.get_config(hide_secrets=False)
        user_config = {
            **user_config,
            "runtime": {**user_config["runtime"], "auto_reload": "off"},
        }

        if self.redirect_console_to_browser:
            from marimo._messaging.thread_local_streams import (
                install_thread_local_proxies,
            )
            install_thread_local_proxies()

        # In subprocess EDIT mode the subprocess registers formatters itself;
        # we do it eagerly here since we share the host process.
        from marimo._output.formatters.formatters import register_formatters
        register_formatters(theme=self.config_manager.theme)

        is_edit_mode = self.mode == kernel_mod.SessionMode.EDIT

        self.kernel_task = threading.Thread(
            target=runtime.launch_kernel,
            args=(
                self.queue_manager.control_queue,
                self.queue_manager.set_ui_element_queue,
                self.queue_manager.completion_queue,
                self.queue_manager.input_queue,
                self.queue_manager.stream_queue,
                None,  # socket_addr — no subprocess pipe
                is_edit_mode,
                self.configs,
                self.app_metadata,
                user_config,
                self._virtual_file_storage,
                self.redirect_console_to_browser,
                None,  # win32 interrupt queue
                None,  # profile path
                GLOBAL_SETTINGS.LOG_LEVEL,
            ),
            daemon=True,
            name="marimo-kernel",
        )
        self.kernel_task.start()

    kernel_mod.KernelManagerImpl.start_kernel = patched_start_kernel
