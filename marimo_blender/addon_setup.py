import importlib.util
import logging
import os
import pkgutil
import shutil
import subprocess
import sys
import threading
import traceback
from typing import Any, Callable


def _invoke_callback(callback=None, *args):
    if callback is None:
        return
    try:
        callback(*args)
    except Exception as e:
        logging.exception("Callback failed:", exc_info=e)


class Executor:
    """Run a function or a subprocess in a daemon thread, with line-by-line
    stdout callback and a finally callback."""

    def __init__(self):
        self._is_running = False
        self._return_value = None
        self._exception = None
        self._process: subprocess.Popen | None = None
        self._exit_code = -1
        self._command_line = ''

    def exec_function(self, function, *args, line_callback=None, finally_callback=None):
        def _run_background():
            try:
                self._return_value = function(*args)
            except Exception as exception:
                self._exception = exception
                self.write_exception(exception, line_callback=line_callback)
            finally:
                self._is_running = False
                _invoke_callback(finally_callback, self)

        self._is_running = True
        self._return_value = None
        self._exception = None

        thread = threading.Thread(target=_run_background, daemon=True)
        thread.start()

    @staticmethod
    def write_exception(exception: Exception, line_callback=None):
        if exception is None:
            return
        for line in (l for f in traceback.format_exception(exception) for l in f.splitlines()):
            _invoke_callback(line_callback, line)

    def exec_command(self, *args, env=None, line_callback=None, finally_callback: Callable[["Executor"], Any] = None):
        if self.is_running:
            raise ValueError(f"Process is running: pid={self._process.pid}")

        self._exit_code = -1
        self._command_line = ' '.join(args)
        self._process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)

        def _enqueue_output():
            encoding = sys.getdefaultencoding()
            assert self._process is not None
            input_text_io = self._process.stdout
            assert input_text_io is not None

            while self._process.poll() is None:
                for buffer in iter(input_text_io.readline, b''):
                    text = buffer.decode(encoding).rstrip()
                    _invoke_callback(line_callback, text)

            input_text_io.close()
            self._exit_code = self._process.poll()
            self._process = None

        self.exec_function(_enqueue_output, finally_callback=finally_callback)

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def return_value(self):
        return self._return_value

    @property
    def exception(self) -> Exception:
        return self._exception

    @property
    def command_line(self) -> str:
        return self._command_line

    @property
    def exit_code(self) -> int:
        return self._exit_code


class Installer(Executor):
    """Install/uninstall marimo into Blender's site-packages.

    Installs prefer ``uv`` (``uv pip install --python <blender-python>
    --target <site-packages>``), which is 10–100× faster than pip at
    resolving and downloading the transitive closure. A system ``uv`` on
    ``PATH`` (or in a common install dir) is used when present, otherwise an
    importable ``uv`` whose binary actually exists. When neither is available
    installs fall back to plain ``pip``, which is always present in Blender's
    Python. Either way wheels land in a location already on Blender's
    ``sys.path``.
    """

    # marimo declares its full transitive dep set in its own Requires-Dist, so
    # we let pip resolve those for us.
    dependencies = [
        "marimo",
    ]

    def get_required_modules(self) -> dict[str, bool]:
        modules = {d.split(">=")[0].strip(): False for d in self.dependencies}
        for m in pkgutil.iter_modules():
            if m.name in modules:
                modules[m.name] = True
        return modules

    @staticmethod
    def _find_system_uv() -> str | None:
        """Locate a ``uv`` binary, tolerating a GUI-launched Blender's PATH.

        ``shutil.which`` only searches ``os.environ["PATH"]``. When Blender
        is launched from Finder/Dock (rather than a terminal) macOS gives it
        a minimal PATH that omits the dirs where uv is usually installed
        (Homebrew's ``/opt/homebrew/bin``, ``~/.local/bin``, ``~/.cargo/bin``,
        …) because those are only added by the shell's startup files. So we
        fall back to probing the common install locations directly.
        """
        found = shutil.which('uv')
        if found:
            return found
        exe = 'uv.exe' if os.name == 'nt' else 'uv'
        candidates = [
            '/opt/homebrew/bin',  # Homebrew on Apple Silicon
            '/usr/local/bin',  # Homebrew on Intel / manual installs
            os.path.expanduser('~/.local/bin'),  # uv's own installer
            os.path.expanduser('~/.cargo/bin'),  # cargo install uv
        ]
        for directory in candidates:
            path = os.path.join(directory, exe)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return None

    @staticmethod
    def _importable_uv_has_binary() -> bool:
        """Whether an importable ``uv`` module can actually find its binary.

        The ``uv`` *module* can be importable while the ``uv`` *binary* it
        execs is absent — e.g. a prior ``pip install --target uv`` whose
        scripts never landed where ``find_uv_bin`` looks. In that state
        ``python -m uv`` raises ``UvNotFound``, so the module must not be
        treated as a usable uv.
        """
        try:
            import uv
            uv.find_uv_bin()
            return True
        except Exception:
            return False

    @classmethod
    def _uv_command(cls) -> list[str] | None:
        """How to invoke uv, preferring a uv already on the system.

        Returns ``["<path>"]`` for a uv on ``PATH`` (or a common install
        location), ``[python, "-m", "uv"]`` if the uv package is importable
        *and* its binary exists, or ``None`` when no usable uv is available
        (callers fall back to pip).
        """
        system_uv = cls._find_system_uv()
        if system_uv:
            return [system_uv]
        if importlib.util.find_spec('uv') is not None and cls._importable_uv_has_binary():
            return [sys.executable, '-m', 'uv']
        return None

    @classmethod
    def _describe_uv(cls) -> str:
        """Human-readable note for the log box about which installer is used."""
        system_uv = cls._find_system_uv()
        if system_uv:
            return f"Using system uv: {system_uv}"
        if cls._uv_command() is not None:
            return f"Using bundled uv: {sys.executable} -m uv"
        return f"No usable uv found — falling back to pip: {sys.executable} -m pip"

    @staticmethod
    def _subprocess_env(site_packages_path: str | None) -> dict[str, str] | None:
        """Env that lets a fresh ``python -m uv`` import a uv installed into
        the extension's ``--target`` site-packages (not on a subprocess's
        default ``sys.path``)."""
        if not site_packages_path:
            return None
        env = dict(os.environ)
        existing = env.get('PYTHONPATH', '')
        env['PYTHONPATH'] = site_packages_path + os.pathsep + existing if existing else site_packages_path
        return env

    def _run_command_chain(self, commands, env, line_callback, finally_callback) -> None:
        """Run subprocess commands sequentially, aborting if one fails.

        ``line_callback`` is forwarded to every command; ``finally_callback``
        fires exactly once, after the last command or the first failure.
        """
        if not commands:
            _invoke_callback(finally_callback, self)
            return

        head, *tail = commands

        def _after(executor: "Executor") -> None:
            if executor.exit_code != 0:
                _invoke_callback(finally_callback, executor)
                return
            self._run_command_chain(tail, env, line_callback, finally_callback)

        self.exec_command(*head, env=env, line_callback=line_callback, finally_callback=_after)

    def _install_commands(self, packages, target_option) -> list[list[str]]:
        """Command chain to install ``packages``.

        Uses uv when one is genuinely usable (see :meth:`_uv_command`).
        Otherwise falls back to plain ``pip install`` — always present in
        Blender's Python — running ``ensurepip`` first only if pip is missing.
        """
        uv = self._uv_command()
        if uv is not None:
            return [[
                *uv, 'pip', 'install',
                '--python', sys.executable,
                *target_option,
                *packages,
            ]]

        commands = []
        if importlib.util.find_spec('pip') is None:
            commands.append([sys.executable, '-m', 'ensurepip'])
        commands.append([
            sys.executable, '-m', 'pip', 'install',
            *target_option,
            '--disable-pip-version-check',
            '--no-input',
            '--exists-action', 'i',
            '--upgrade',
            *packages,
        ])
        return commands

    def install_python_modules(self, line_callback=None, finally_callback=None):
        site_packages_path = next((p for p in sys.path if p.endswith('site-packages')), None)
        target_option = ['--target', site_packages_path] if site_packages_path else []

        # Migration path: users coming from the old vendored-marimo addon
        # have a symlink at site-packages/marimo pointing into the addon dir.
        # pip --target then trips over `shutil.move` on a symlink. Clear it
        # before installing.
        if site_packages_path:
            marimo_path = os.path.join(site_packages_path, 'marimo')
            if os.path.islink(marimo_path) or (os.path.exists(marimo_path) and not os.path.isdir(marimo_path)):
                os.unlink(marimo_path)
                print(f'Removed legacy marimo symlink: {marimo_path}')

        missing = [name for name, installed in self.get_required_modules().items() if not installed]
        if not missing:
            # Still run the installer so the user gets feedback in the log box.
            missing = list(self.dependencies)

        _invoke_callback(line_callback, self._describe_uv())
        self._run_command_chain(
            self._install_commands(missing, target_option),
            self._subprocess_env(site_packages_path),
            line_callback,
            finally_callback,
        )

    def install_python_module(self, module_name, line_callback=None, finally_callback=None):
        site_packages_path = next((p for p in sys.path if p.endswith('site-packages')), None)
        target_option = ['--target', site_packages_path] if site_packages_path else []
        _invoke_callback(line_callback, self._describe_uv())
        self._run_command_chain(
            self._install_commands(module_name.split(), target_option),
            self._subprocess_env(site_packages_path),
            line_callback,
            finally_callback,
        )

    def uninstall_python_modules(self, line_callback=None, finally_callback=None):
        self.exec_command(
            sys.executable, '-m', 'pip', 'uninstall',
            '--yes',
            *[name for name, installed in self.get_required_modules().items() if installed],
            line_callback=line_callback, finally_callback=finally_callback
        )

    def list_python_modules(self, line_callback=None, finally_callback=None):
        self.exec_command(
            sys.executable, '-m', 'pip', 'list', '-v',
            line_callback=line_callback, finally_callback=finally_callback
        )


def _open_app_window(url: str, width: int = 340, height: int = 240):
    """Open *url* in a chromeless app window. Falls back to webbrowser."""
    import os
    import subprocess
    chrome_candidates = [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
        'google-chrome',
        'chromium-browser',
        'chromium',
    ]
    profile_dir = os.path.expanduser('~/.marimo-blender-chrome-profile')

    for path in chrome_candidates:
        try:
            subprocess.Popen([
                path,
                f'--app={url}',
                f'--window-size={width},{height}',
                f'--user-data-dir={profile_dir}',
            ])
            return
        except (FileNotFoundError, OSError):
            continue
    import webbrowser
    webbrowser.open(url)


class Server(Executor):
    def __init__(self):
        super().__init__()
        self._port: int | None = None
        self._app_view: bool = False

    def start(self, port, filename, mode=None, line_callback=None, finally_callback=None):
        # Apply marimo patches + register the main-thread pump on Blender's
        # main thread BEFORE we spawn the server thread. The executor must be
        # registered before any session/kernel construction, and
        # bpy.app.timers.register requires the main thread.
        from . import main_thread, marimo_patches
        marimo_patches.apply()
        main_thread.ensure_registered()

        def server_thread_function(port: int, filename: str, mode):
            import signal
            from marimo._server.start import start
            from marimo._utils.net import find_free_port
            from marimo._session.model import SessionMode
            from marimo._server.tokens import AuthToken
            if mode is None:
                mode = SessionMode.EDIT
            from marimo._server.workspace import EmptyWorkspace, infer_workspace
            from marimo._cli.parse_args import parse_args

            # marimo + uvicorn install signal handlers (signal.signal and
            # loop.add_signal_handler). Both only work on the main thread, but
            # we run in a daemon thread inside Blender. Swallow the errors so
            # the server can finish starting.
            original_signal = signal.signal

            def patched_signal(signum, handler):
                try:
                    return original_signal(signum, handler)
                except ValueError:
                    return None

            signal.signal = patched_signal

            loop_patches = []
            try:
                from asyncio import unix_events
            except ImportError:
                unix_events = None
            if unix_events is not None:
                loop_cls = unix_events._UnixSelectorEventLoop
                orig_add = loop_cls.add_signal_handler
                orig_remove = loop_cls.remove_signal_handler

                def safe_add(self, sig, callback, *args):
                    try:
                        return orig_add(self, sig, callback, *args)
                    except (ValueError, RuntimeError):
                        return None

                def safe_remove(self, sig):
                    try:
                        return orig_remove(self, sig)
                    except (ValueError, RuntimeError):
                        return False

                loop_cls.add_signal_handler = safe_add
                loop_cls.remove_signal_handler = safe_remove
                loop_patches.append((loop_cls, orig_add, orig_remove))

            _registry_path = None
            try:
                # Blender's cwd is often `/` (or the app bundle) when launched
                # from the Dock, which marimo can't write to. Default new
                # notebooks to the user's home dir.
                if not filename:
                    os.chdir(os.path.expanduser("~"))

                self._port = find_free_port(port, addr="127.0.0.1")
                self._app_view = (mode == SessionMode.RUN)

                if self._app_view:
                    import threading as _threading
                    _snap_port = self._port
                    def _delayed_open():
                        import time
                        time.sleep(0.8)
                        _open_app_window(f"http://127.0.0.1:{_snap_port}")
                    _threading.Thread(target=_delayed_open, daemon=True).start()

                # Write directly to the marimo-pair server registry so
                # auto-discovery works (equivalent to running marimo --no-token).
                # We bypass marimo's ServerRegistryWriter and write the JSON
                # file directly to guarantee it's created before start() blocks.
                try:
                    import json
                    from datetime import datetime, timezone
                    from pathlib import Path
                    _servers_dir = (
                        Path(os.environ["XDG_STATE_HOME"])
                        if "XDG_STATE_HOME" in os.environ
                        else Path.home() / ".local" / "state"
                    ) / "marimo" / "servers"
                    _servers_dir.mkdir(parents=True, exist_ok=True)
                    _registry_path = _servers_dir / f"127.0.0.1_{self._port}.json"
                    _registry_path.write_text(json.dumps({
                        "server_id": f"127.0.0.1:{self._port}",
                        "pid": os.getpid(),
                        "host": "127.0.0.1",
                        "port": self._port,
                        "base_url": "",
                        "started_at": datetime.now(timezone.utc).isoformat(),
                        "version": "unknown",
                    }, indent=2))
                except Exception:
                    _registry_path = None

                workspace = infer_workspace(filename) if filename else EmptyWorkspace()
                start(
                    workspace=workspace,
                    mode=mode,
                    development_mode=True,
                    quiet=False,
                    include_code=True,
                    ttl_seconds=None,
                    headless=(mode == SessionMode.RUN),
                    port=self._port,
                    host="127.0.0.1",
                    proxy=None,
                    watch=False,
                    cli_args=parse_args(()),
                    argv=[],
                    auth_token=AuthToken(""),
                    redirect_console_to_browser=False,
                    skew_protection=False,
                )
            finally:
                if _registry_path is not None:
                    try:
                        _registry_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                signal.signal = original_signal
                for loop_cls, orig_add, orig_remove in loop_patches:
                    loop_cls.add_signal_handler = orig_add
                    loop_cls.remove_signal_handler = orig_remove
                self._port = None
                self._app_view = False
        self.exec_function(server_thread_function, port, filename, mode, line_callback=line_callback, finally_callback=finally_callback)

    def open_browser(self):
        url = f"http://127.0.0.1:{self._port}"
        if self._app_view:
            _open_app_window(url)
        else:
            import webbrowser
            webbrowser.open(url)

    def stop(self):
        """Shut down marimo's kernel sessions, then signal uvicorn to exit.

        Stopping the sessions first is essential, not cosmetic. marimo only
        wires SessionManager.shutdown() (which sends StopKernelCommand to each
        kernel) to its signal-based InterruptHandler (SIGINT/SIGTERM). We
        trigger shutdown programmatically via `should_exit`, so no signal ever
        fires and the kernel is never told to stop. Its control loop then
        stays blocked forever in `run_in_executor(None, control_queue.get)`.
        That executor worker is a *non-daemon* thread, so Python's
        `concurrent.futures.thread._python_exit` atexit handler blocks
        joining it at interpreter shutdown — freezing Blender on quit.

        Sending StopKernelCommand lets the control loop break, the kernel's
        asyncio.run() finish, and its default executor shut down cleanly.
        """
        from . import marimo_patches
        uv_server = marimo_patches.get_running_uvicorn_server()
        if uv_server is None:
            return

        try:
            app = uv_server.config.app
            session_manager = app.state.session_manager
        except Exception as exc:  # noqa: BLE001
            logging.warning("Could not reach marimo session manager: %s", exc)
            session_manager = None

        if session_manager is not None:
            # Send StopKernelCommand directly first so the kernel control loop
            # exits even if the broader shutdown() trips on event-loop-bound
            # cleanup (Session.close() runs room.close() before close_kernel()).
            # For threading kernels close_kernel() is just a thread-safe queue
            # put and does not block.
            for session in list(session_manager.sessions.values()):
                try:
                    session._kernel_manager.close_kernel()
                except Exception as exc:  # noqa: BLE001
                    logging.warning("close_kernel failed: %s", exc)
            try:
                session_manager.shutdown()
            except Exception as exc:  # noqa: BLE001
                logging.warning("marimo session manager shutdown failed: %s", exc)

        uv_server.should_exit = True

    @property
    def port(self) -> int | None:
        return self._port


installer = Installer()
server = Server()
