import signal
from types import FrameType
from typing import Callable

from core.logging import get_logger


logger = get_logger(__name__)


class GracefulShutdown:
    """
    Handles graceful application shutdown.

    This prevents abrupt termination and gives the application
    a chance to clean up resources.
    """

    def __init__(self) -> None:
        self._shutdown_requested = False
        self._callbacks: list[Callable[[], None]] = []

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_requested

    def register_callback(
        self,
        callback: Callable[[], None],
    ) -> None:
        """Register a cleanup callback."""

        self._callbacks.append(callback)

    def request_shutdown(
        self,
        signum: int | None = None,
        frame: FrameType | None = None,
    ) -> None:
        """Request application shutdown."""

        if self._shutdown_requested:
            return

        self._shutdown_requested = True

        logger.info(
            "Shutdown requested"
            + (
                f" by signal {signum}"
                if signum is not None
                else ""
            )
        )

        self._run_callbacks()

    def _run_callbacks(self) -> None:
        """Execute registered cleanup callbacks."""

        for callback in self._callbacks:
            try:
                callback()

            except Exception:
                logger.exception(
                    "Error during shutdown callback"
                )

    def install_signal_handlers(self) -> None:
        """Install SIGINT and SIGTERM handlers."""

        signal.signal(
            signal.SIGINT,
            self.request_shutdown,
        )

        if hasattr(signal, "SIGTERM"):
            signal.signal(
                signal.SIGTERM,
                self.request_shutdown,
            )

        logger.debug(
            "Signal handlers installed"
        )