from __future__ import annotations

from pulse.core.notify.notifier import MultiNotifier, Notification


class _Sink:
    def __init__(self) -> None:
        self.received: list[Notification] = []

    def send(self, message: Notification) -> None:
        self.received.append(message)


class _FailingSink:
    def send(self, message: Notification) -> None:  # noqa: ARG002
        raise RuntimeError("boom")


def test_multi_notifier_fanout_keeps_healthy_sink() -> None:
    ok_sink = _Sink()
    notifier = MultiNotifier([_FailingSink(), ok_sink])
    message = Notification(level="info", title="t", content="c")
    notifier.send(message)
    assert ok_sink.received == [message]
