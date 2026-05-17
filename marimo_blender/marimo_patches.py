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

    _PATCHED = True


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
                self.config_manager.get_config(hide_secrets=False),
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
