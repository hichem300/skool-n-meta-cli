from typing import Protocol

from skool_data.models import RunStatus


class CaptureReporter(Protocol):
    def run_started(self, total: int) -> None: ...

    def endpoint_started(self, name: str, index: int, total: int) -> None: ...

    def endpoint_succeeded(
        self, name: str, elapsed: float, observation_count: int
    ) -> None: ...

    def endpoint_failed(self, name: str, elapsed: float, reason: str) -> None: ...

    def run_finished(self, status: RunStatus, elapsed: float) -> None: ...


class NullCaptureReporter:
    def run_started(self, total: int) -> None:
        pass

    def endpoint_started(self, name: str, index: int, total: int) -> None:
        pass

    def endpoint_succeeded(
        self, name: str, elapsed: float, observation_count: int
    ) -> None:
        pass

    def endpoint_failed(self, name: str, elapsed: float, reason: str) -> None:
        pass

    def run_finished(self, status: RunStatus, elapsed: float) -> None:
        pass
