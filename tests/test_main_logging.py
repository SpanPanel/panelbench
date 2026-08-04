"""Logging setup for the entry point.

ebus-sdk's `homie` logger pins itself to INFO at import time and dumps every
node's full property table through pformat() while the device graph is built.
That is thousands of lines per panel, and because the logger sets its own level
it appeared no matter what --log-level the operator asked for. The entry point
filters it on the root handler; these tests pin that behaviour down."""

import logging
from collections.abc import Callable, Iterator

import pytest

from span_panel_simulator.__main__ import _configure_logging, _NoisyDependencyFilter


def _record(name: str, level: int) -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1, msg="x", args=(), exc_info=None
    )


def test_homie_info_is_dropped_at_the_default_threshold() -> None:
    noise_filter = _NoisyDependencyFilter(logging.WARNING)
    assert not noise_filter.filter(_record("homie", logging.INFO))


def test_homie_warnings_and_errors_always_pass() -> None:
    noise_filter = _NoisyDependencyFilter(logging.WARNING)
    assert noise_filter.filter(_record("homie", logging.WARNING))
    assert noise_filter.filter(_record("homie", logging.ERROR))


def test_aiohttp_access_info_is_dropped_at_the_default_threshold() -> None:
    noise_filter = _NoisyDependencyFilter(logging.WARNING)
    assert not noise_filter.filter(_record("aiohttp.access", logging.INFO))


def test_debug_threshold_lets_the_schema_dump_through() -> None:
    """`--log-level DEBUG` (scripts/run-local.sh --debug) must still show it."""
    noise_filter = _NoisyDependencyFilter(logging.DEBUG)
    assert noise_filter.filter(_record("homie", logging.INFO))
    assert noise_filter.filter(_record("aiohttp.access", logging.INFO))


def test_simulator_own_logs_are_untouched() -> None:
    noise_filter = _NoisyDependencyFilter(logging.WARNING)
    assert noise_filter.filter(_record("span_panel_simulator.app", logging.INFO))
    assert noise_filter.filter(_record("span_panel_simulator.panel", logging.DEBUG))


def test_child_loggers_of_a_noisy_name_are_filtered_too() -> None:
    noise_filter = _NoisyDependencyFilter(logging.WARNING)
    assert not noise_filter.filter(_record("homie.device", logging.INFO))


@pytest.fixture
def configure_logging() -> Iterator[Callable[[str], None]]:
    """Hand back a callable that runs the entry point's real logging setup.

    `logging.basicConfig` no-ops when the root logger already has a handler, and
    pytest's logging plugin installs one for the call phase — so the handlers are
    cleared immediately before the call, matching a fresh process. The originals
    are restored afterwards."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level

    def run(log_level: str) -> None:
        root.handlers = []
        _configure_logging(log_level)

    yield run
    root.handlers, root.level = saved_handlers, saved_level


def test_configure_logging_suppresses_the_real_homie_logger(
    configure_logging: Callable[[str], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end over the wiring, not just the filter: after the entry point
    configures logging, an INFO record on the real `homie` logger produces no
    output while the simulator's own INFO record still does."""
    configure_logging("INFO")

    logging.getLogger("homie").info("reason=nodeDescriptionNode,node={...}")
    logging.getLogger("homie").warning("broker trouble")
    logging.getLogger("span_panel_simulator.app").info("Reload complete")

    err = capsys.readouterr().err
    assert "nodeDescriptionNode" not in err
    assert "broker trouble" in err
    assert "Reload complete" in err


def test_configure_logging_at_debug_keeps_the_schema_dump(
    configure_logging: Callable[[str], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("DEBUG")

    logging.getLogger("homie").info("reason=nodeDescriptionNode,node={...}")

    assert "nodeDescriptionNode" in capsys.readouterr().err


def test_sdk_leaves_homie_log_configuration_to_the_application() -> None:
    """The premise the noisy-dependency filter defends against is gone.

    The SDK used to pin ``setLevel(INFO)`` on the ``homie`` logger at import, which
    is what the filter exists to survive. It no longer does: the logger is left at
    NOTSET with ``propagate=True``, so log configuration belongs to the application.

    Two SDK changes retired the noise this guarded. Transport-free trees — which is
    how this simulator builds its device graph — now report a missing client at
    ``debug`` rather than ``warning`` (GH #11 / PR #20), and the level is no longer
    forced at import.

    This is the tripwire in reverse: if a future release re-pins the level, the
    filter's threshold becomes load-bearing again and this test says so.
    """
    import ebus_sdk.homie  # noqa: F401  -- imported for the side effect under test

    homie = logging.getLogger("homie")
    assert homie.level == logging.NOTSET
    assert homie.propagate is True
