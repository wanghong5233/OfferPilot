from __future__ import annotations

from pulse.core.channel import CliChannelAdapter


def test_cli_parse_incoming_returns_message() -> None:
    adapter = CliChannelAdapter()
    message = adapter.parse_incoming("  hello pulse  ")
    assert message is not None
    assert message.channel == "cli"
    assert message.user_id == "local-user"
    assert message.text == "hello pulse"


def test_cli_dispatch_invokes_handler() -> None:
    adapter = CliChannelAdapter()
    called: list[str] = []
    adapter.set_handler(lambda message: called.append(message.text))
    message = adapter.parse_incoming("run")
    assert message is not None
    adapter.dispatch(message)
    assert called == ["run"]


def test_cli_run_loop_stops_on_exit() -> None:
    adapter = CliChannelAdapter()
    received: list[str] = []
    adapter.set_handler(lambda message: received.append(message.text))

    inputs = iter(["first", "", "second", "exit"])

    def _fake_input(_: str) -> str:
        return next(inputs)

    count = adapter.run_interactive_loop(input_func=_fake_input)
    assert count == 2
    assert received == ["first", "second"]
