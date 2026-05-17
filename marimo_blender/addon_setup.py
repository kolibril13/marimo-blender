import contextlib
import io
import logging
import pkgutil
import subprocess
import os
import sys
import threading
import traceback
from typing import Iterable, Callable, Any


def _invoke_callback(callback=None, *args):
    if callback is None:
        return
    try:
        callback(*args)
    except Exception as e:
        logging.exception("Callback failed:", exc_info=e)


class Executor:
    def __init__(self):
        self._is_running = False
        self._return_value = None
        self._exception = None
        self._process = None
        self._exit_code = -1
        self._command_line = ''

    def exec_function(self, function, *args, line_callback=None, finally_callback=None):
        class OutBuffer(io.StringIO):
            def write(self, text: str) -> int:
                _invoke_callback(line_callback, text)
                return super().write(text)

            def writelines(self, lines: Iterable[str]) -> None:
                lines_buffer = list(l for l in lines)
                for line in lines_buffer:
                    _invoke_callback(line_callback, line)
                return super().writelines(lines_buffer)

        def _run_background():
            buffer = OutBuffer()
            try:
                with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
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

        thread = threading.Thread(target=_run_background)
        thread.daemon = True
        thread.start()

    @staticmethod
    def write_exception(exception: Exception, line_callback=None):
        if exception is None:
            return
        for line in (l for f in traceback.format_exception(exception) for l in f.splitlines()):
            _invoke_callback(line_callback, line)

    def exec_command(self, *args, line_callback=None, finally_callback: Callable[["Executor"], Any] = None):
        if self.is_running:
            raise ValueError(f"Process is running: pid={self._process.pid}")

        self._exit_code = -1
        self._command_line = ' '.join(args)
        self._process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        def _enqueue_output():
            encoding = sys.getdefaultencoding()
            input_text_io = self._process.stdout

            buffer: bytearray
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
    def command_line(self) -> int:
        return self._command_line

    @property
    def exit_code(self) -> int:
        return self._exit_code


class Installer(Executor):
    dependencies = [
        # For maintainable cli
        "click>=8.0,<9",
        # For python 3.8 compatibility
        # "importlib_resources>=5.10.2; python_version < \"3.9\"",
        # code completion
        "jedi>=0.18.0",
        # compile markdown to html
        "markdown>=3.4,<4",
        # add features to markdown
        "pymdown-extensions>=9.0,<11",
        # syntax highlighting of code in markdown
        "pygments>=2.13,<3",
        # for reading, writing configs
        "tomlkit>= 0.12.0",
        # web server
        # - 0.22.0 introduced timeout-graceful-shutdown, which we use
        "uvicorn >= 0.22.0",
        # web framework
        # - 0.26.1 introduced lifespans, which we use
        # - starlette 0.36.0 introduced a bug
        "starlette>=0.26.1,!=0.36.0",
        # websockets for use with starlette
        "websockets >= 10.0.0,<13.0.0",
        # python <=3.10 compatibility
        # "typing_extensions>=4.4.0; python_version < \"3.10\"",
        # for rst parsing
        "docutils>=0.17.0",
        # for cell formatting; if user version is not compatible, no-op
        # so no lower bound needed
        "black",
        "marimo",
    ]

    if sys.version_info < (3, 9):
        dependencies.append("importlib_resources>=5.10.2")

    if sys.version_info < (3, 10):
        dependencies.append("typing_extensions>=4.4.0")

    def get_required_modules(self) -> dict[str, bool]:
        modules = {d.split(">=")[0].strip(): False for d in self.dependencies}
        for m in pkgutil.iter_modules():
            if m.name in modules:
                modules[m.name] = True
            elif m.name == "pymdownx":
                modules["pymdown-extensions"] = True
        return modules

    def install_python_modules(self, line_callback=None, finally_callback=None):

        site_packages_path = next((p for p in sys.path if p.endswith('site-packages')), None)
        target_option = ['--target', site_packages_path] if site_packages_path else []

        def cleanup_legacy_marimo_symlink():
            if not site_packages_path:
                return
            marimo_path = os.path.join(site_packages_path, 'marimo')
            if os.path.islink(marimo_path) or (os.path.exists(marimo_path) and not os.path.isdir(marimo_path)):
                os.unlink(marimo_path)
                print(f'Removed legacy marimo symlink: {marimo_path}')

        cleanup_legacy_marimo_symlink()

        self.exec_command(
            sys.executable, '-m', 'ensurepip',
            line_callback=line_callback,
            finally_callback=lambda e: e.exec_command(
                sys.executable, '-m', 'pip', 'install',
                *target_option,
                '--disable-pip-version-check',
                '--no-input',
                '--exists-action', 'i',
                '--upgrade',
                *[name for name, installed in self.get_required_modules().items() if not installed],
                line_callback=line_callback,
                finally_callback=finally_callback,
            )
        )

    def install_python_module(self, module_name, line_callback=None, finally_callback=None):
        self.exec_command(
            sys.executable, '-m', 'pip', 'install',
            '--disable-pip-version-check',
            '--no-input',
            '--exists-action', 'i',
            module_name,
            line_callback=line_callback,
            finally_callback=finally_callback)

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


class Server(Executor):
    def __init__(self):
        super().__init__()
        self._port = None

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

        thread = threading.Thread(target=_run_background)
        thread.daemon = True
        thread.start()

    def start(self, port, filename, line_callback=None, finally_callback=None):
        # Apply marimo patches + register the main-thread pump on Blender's
        # main thread BEFORE we spawn the server thread, so the executor is
        # registered before any session/kernel construction, and so the
        # bpy.app.timers.register call (which requires the main thread)
        # happens here.
        from . import main_thread, marimo_patches
        marimo_patches.apply()
        main_thread.ensure_registered()

        def server_thread_function(port: int, filename: str):
            import signal
            from marimo._server.start import start
            from marimo._utils.net import find_free_port
            from marimo._session.model import SessionMode
            from marimo._server.tokens import AuthToken
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

            try:
                self._port = find_free_port(port, addr="127.0.0.1")
                workspace = infer_workspace(filename) if filename else EmptyWorkspace()
                start(
                    workspace=workspace,
                    mode=SessionMode.EDIT,
                    development_mode=True,
                    quiet=False,
                    include_code=True,
                    ttl_seconds=None,
                    headless=False,
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
                signal.signal = original_signal
                for loop_cls, orig_add, orig_remove in loop_patches:
                    loop_cls.add_signal_handler = orig_add
                    loop_cls.remove_signal_handler = orig_remove
        self.exec_function(server_thread_function, port, filename, line_callback=line_callback, finally_callback=finally_callback)

    def stop(self):
        raise NotImplementedError()

    @property
    def port(self):
        return self._port


installer = Installer()
server = Server()
