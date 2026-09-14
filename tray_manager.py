"""System tray support for Codex Bridge Toolkit.

Imports are lazy so source-mode diagnostics/tests still work on systems where
pystray/Pillow are not installed yet.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable


class TrayController:
    def __init__(
        self,
        root,
        icon_path: Path,
        on_restore: Callable[[], None],
        on_exit_restore: Callable[[], None],
    ) -> None:
        self.root = root
        self.icon_path = Path(icon_path)
        self.on_restore = on_restore
        self.on_exit_restore = on_exit_restore
        self._icon = None
        self._thread: threading.Thread | None = None

    @staticmethod
    def available() -> bool:
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
            return True
        except Exception:
            return False

    @property
    def running(self) -> bool:
        return self._icon is not None

    def show(self) -> bool:
        if self._icon is not None:
            return True
        try:
            import pystray
            from PIL import Image

            if not self.icon_path.exists():
                return False
            image = Image.open(self.icon_path)

            def _restore(_icon=None, _item=None):
                self.root.after(0, self.on_restore)

            def _exit(_icon=None, _item=None):
                self.root.after(0, self.on_exit_restore)

            menu = pystray.Menu(
                pystray.MenuItem("打开 Codex Bridge Toolkit", _restore, default=True),
                pystray.MenuItem("退出并恢复 Codex 直连", _exit),
            )
            self._icon = pystray.Icon(
                "CodexBridgeToolkit",
                image,
                "Codex Bridge Toolkit",
                menu,
            )
            self._thread = threading.Thread(target=self._icon.run, daemon=True)
            self._thread.start()
            return True
        except Exception:
            self._icon = None
            self._thread = None
            return False

    def stop(self) -> None:
        icon, self._icon = self._icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass

    def hide_window_to_tray(self) -> bool:
        if not self.show():
            return False
        self.root.withdraw()
        return True

    def restore_window(self) -> None:
        try:
            self.root.deiconify()
            self.root.state("normal")
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass
