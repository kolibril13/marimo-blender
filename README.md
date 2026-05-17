# marimo-blender



A Blender add-on that runs a [marimo](https://marimo.io) reactive notebook server inside Blender, so cells can `import bpy` and drive the running scene live.

## Quick start

1. Install the add-on as a Blender extension.
2. Open the **N** sidebar in the 3D viewport → **Notebook** panel.
3. Click **Install Python Modules** once to pull marimo + deps from pip into Blender's extension site-packages.
4. Click **Start Notebook Server**. The browser opens to `http://127.0.0.1:2718`.
5. In a cell:
   ```python
   import bpy
   bpy.ops.mesh.primitive_uv_sphere_add(radius=0.1, location=(1, 2, 0))
   ```

## How it works

The hard part of embedding marimo in Blender isn't running the web server — it's making cells able to touch the Blender scene without crashing the editor. This section explains the architecture and the reason each piece exists.

### The fundamental constraint: `_bpy` only exists inside the Blender executable

`bpy` is a Python wrapper around `_bpy`, a C extension that is compiled into the Blender binary itself. A spawned Python subprocess — even one using Blender's bundled Python interpreter — cannot import `_bpy`, because the symbols simply aren't there outside the running Blender process. Anything that wants to call `bpy` must live in the Blender process.

Marimo's default architecture conflicts with this. For each notebook session in EDIT mode, marimo spawns a `multiprocessing.Process` running `marimo._runtime.runtime.launch_kernel`. That subprocess does the cell execution. `import bpy` in such a subprocess fails with `ModuleNotFoundError: No module named '_bpy'`.

So the add-on has to do three things that fight marimo's grain:

1. Run the marimo HTTP server **inside Blender** (background thread, since uvicorn blocks).
2. Run each notebook's **kernel in-thread**, not in a subprocess, so `_bpy` is visible.
3. **Schedule actual cell code on Blender's main thread**, because the `bpy` API is not thread-safe — concurrent writes from a background thread crash the viewport redraw with depsgraph races.

### 1. Server in a background thread

`marimo_blender/addon_setup.py` (`Server.start`) launches marimo's uvicorn server in a daemon thread. Two side-patches are needed because marimo + uvicorn install signal handlers that only work on the main thread:

- `signal.signal(...)` is wrapped to swallow `ValueError` when called off-main-thread (so `initialize_signals()` and the kernel's own SIGINT/SIGTERM installs become no-ops instead of crashes).
- `asyncio._UnixSelectorEventLoop.add_signal_handler` / `remove_signal_handler` are wrapped to swallow `RuntimeError`. Marimo calls `loop.add_signal_handler(SIGINT, …)` inside `_server/api/interrupt.py` during server startup; without this patch the lifespan startup throws and the server hangs at "starting…".

`host` is set to `127.0.0.1` so marimo's auto-open-browser lifespan produces a valid URL (with an empty host it builds `http://:port`, which the browser renders as `about:blank`). `find_free_port` is also called with `addr="127.0.0.1"` so it probes the same interface uvicorn will bind to.

### 2. In-process kernel (the threading-mode patch)

This is the centerpiece. Marimo's `KernelManagerImpl.start_kernel` (`_session/managers/kernel.py`) branches on session mode: EDIT → `multiprocessing.Process`, RUN → `threading.Thread`. Both end up calling the same `runtime.launch_kernel` entrypoint. RUN mode's threading branch already wires up `stream_queue` for kernel→server messages, so the runtime is structurally thread-compatible.

`marimo_blender/marimo_patches.py` monkey-patches marimo to force the threading branch for *all* sessions:

- `QueueManagerImpl.__init__` is patched to always pass `use_multiprocessing=False`, so all queues are `queue.Queue` (shared by reference between the server and kernel threads) and `stream_queue` exists.
- `KernelManagerImpl.start_kernel` is replaced with a version that always spawns a `threading.Thread`, calling `runtime.launch_kernel(..., socket_addr=None, ...)`. With `socket_addr=None`, the runtime uses `QueuePipe(stream_queue)` for output instead of the AF_INET pipe that EDIT mode normally needs.
- `is_edit_mode=True` is still passed, so EDIT-only features (completions, stdin, debugger, render hooks, kernel-context mode) all stay on.

Three subprocess-only side effects are neutralized at module level so the threaded kernel doesn't try to do things only a child process can:

- `runtime.restore_signals = noop` — would call `signal.signal` for every signal.
- `runtime.start_parent_poller = noop` — spins a parent-PID watchdog.
- `os.setsid = noop` — establishes a new POSIX session/process group. Blender doesn't call setsid itself, so a global no-op is safe.

One more subtle patch: `runtime.ThreadSafeStdout` / `ThreadSafeStderr` are subclassed to always pass `forward_os_streams=False`. Otherwise the runtime would use `os.dup2` to redirect Blender's process-global stdout/stderr to the marimo stream — capturing all of Blender's own log output.

`signal.signal` calls inside the runtime (SIGINT/SIGTERM handler installs at the end of `launch_kernel`) are tolerated by the wrapper described in section 1, so we don't have to patch them again.

### 3. Cell execution on Blender's main thread

Threading the kernel solves `import bpy`, but creates a new problem: `bpy` mutations from a background thread race with Blender's main-thread viewport redraw. In practice this means a cell like `bpy.ops.mesh.primitive_uv_sphere_add(...)` can crash Blender mid-frame inside `DEG_iterator_objects_next` (depsgraph mutation during iteration).

The fix follows the [`bpy_jupyter`](https://github.com/Octoframes/bpy_jupyter) pattern: a `bpy.app.timers` callback on the main thread drains a job queue, the kernel thread submits work and blocks on a `threading.Event` until done.

`marimo_blender/main_thread.py`:
- `_drain_callback` runs on Blender's main thread every 1ms, pulling `(fn, args, kwargs, holder, event)` tuples off `_QUEUE` and running them.
- `run_on_main(fn, *args, **kwargs)` is called from the kernel thread, submits the job, and blocks on `event.wait()` until the main thread finishes.
- `ensure_registered()` registers the timer. It is called from `Server.start` (which runs as a Blender operator on the main thread), because `bpy.app.timers.register` requires the main thread.

To make marimo route cell execution through `run_on_main`, the add-on registers a custom `Executor` via marimo's `EntryPointRegistry`:

```python
_EXECUTOR_REGISTRY.register("marimo-blender-main-thread", MainThreadExecutor)
```

`get_executor()` in `_runtime/executor.py` composes registered executors over `DefaultExecutor`, so `MainThreadExecutor.base` is the upstream default. Our `execute_cell` calls `self.base.execute_cell(...)` via `run_on_main` — meaning the cell's `exec(cell.body, glbls)` and final `eval(cell.last_expr, glbls)` both run on Blender's main thread. Bpy reads and writes are therefore safe.

### 4. Thread-local runtime context propagation

The piece that's easy to miss: marimo's runtime context (UI element registry, reactive graph state, current cell ID) is stored in a `_ThreadLocalContext(threading.local)` (`_runtime/context/types.py:201`). When we jump execution to Blender's main thread, that thread's `_THREAD_LOCAL_CONTEXT.runtime_context` is `None` — so `mo.ui.slider(...)` registrations silently land in an empty context and reactivity breaks (the slider moves in the UI, but downstream cells never re-run).

`MainThreadExecutor` therefore snapshots the kernel thread's `runtime_context` before dispatching, installs it on the main thread for the duration of the cell, and restores afterwards. This is what the `_run_with_ctx` wrapper does.

## Trade-offs and limitations

- **Cells block the Blender UI** while they run. Same caveat as `bpy_jupyter`. A long render or heavy numerical loop will freeze the viewport. This is the unavoidable cost of giving cells safe access to the `bpy` API — that API only works on the main thread, and the main thread is also what draws the UI.
- **Top-level `await` in cells executes via the sync path** on the main thread. The async branch in marimo's runner (`cell.is_coroutine()`) routes through `execute_cell_async`; our wrapper hands that off to `loop.run_in_executor` so the kernel's asyncio loop stays responsive, but the cell body itself still runs synchronously on the main thread via `base.execute_cell`. Cells that truly need top-level `await` are not supported.
- **`data:` URI anywidget modules are blocked** by marimo's frontend (e.g. `drawdata.ScatterWidget`). This is an upstream security check baked into marimo's JS bundle, not something the add-on imposes.
- **The marimo monkey-patches target internal APIs** (`KernelManagerImpl.start_kernel`, `QueueManagerImpl.__init__`, `runtime.restore_signals`, …). They're stable across marimo 0.23.x but may need re-validation on future marimo releases. There is no official extension hook for kernel manager selection — see `_session/session.py:147–181`.

## Source map

| File | Role |
|---|---|
| `marimo_blender/__init__.py` | Add-on registration, N-panel UI. |
| `marimo_blender/preferences.py` | The N-panel draw function (`draw_preferences`); operators for install/uninstall/start/stop; `AddonPreferences` (now a thin stub pointing at the sidebar). |
| `marimo_blender/addon_setup.py` | `Installer` (pip wrapper) and `Server` (uvicorn-in-a-thread). The signal/asyncio wrappers and `marimo._server.start.start(...)` call live here. |
| `marimo_blender/marimo_patches.py` | All monkey-patches that force marimo's kernel onto a thread and register the main-thread `Executor`. |
| `marimo_blender/main_thread.py` | The `bpy.app.timers`-driven job queue that runs cell bodies on Blender's main thread. |
