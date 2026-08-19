"""Tests for admission.py (B-408/B-444) and its wiring into network_tools.py.

`tests/conftest.py`'s autouse `_admission_state_never_leaks_across_tests`
fixture already points `NETTOOLS_ADMISSION_DIR` at a fresh `tmp_path` for
every test here, so nothing below needs to set it up by hand -- but several
tests read `os.environ["NETTOOLS_ADMISSION_DIR"]` directly, to pre-hold a
lock file at the exact path `admission.py` itself would use, simulating "a
collection is already in flight" without needing real threads.

Every refusal test carries a positive control (OBS-181): the same code path,
unblocked, must succeed -- proving the test can tell the two states apart,
not merely that *some* value came back.
"""

from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from helpers import install_fake_netmiko, set_device_environment

from agent_nettools import admission
from agent_nettools.network_tools import (
    STATUS_ERROR,
    STATUS_SUCCESS,
    collect_evidence,
    ping_device,
)


def _admission_dir() -> Path:
    path = Path(os.environ["NETTOOLS_ADMISSION_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def _hold_device_slot(device_name: str, *, slot: int = 0):
    """Simulate "a collection is already in flight against this device" by
    holding the exact lock file admission.py's own device-slot pool would
    use, without needing a real second thread or process.
    """

    path = _admission_dir() / f"device.{device_name}.{slot}.lock"
    handle = open(path, "a+b")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


@contextmanager
def _hold_fabric_slot(slot: int):
    path = _admission_dir() / f"fabric.{slot}.lock"
    handle = open(path, "a+b")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


# --------------------------------------------------------------------------- #
# admission.admit(): the per-device cap.
# --------------------------------------------------------------------------- #


def test_admit_succeeds_when_nothing_else_holds_the_device(monkeypatch):
    """Positive control for every refusal test below: the ordinary case."""

    with admission.admit("PE1"):
        pass  # no exception -- admitted.


def test_admit_refuses_a_second_concurrent_collection_against_one_device():
    """B-444's core claim: NETTOOLS_MAX_CONCURRENT_PER_DEVICE=1 (default)
    refuses a second concurrent collection against the SAME device."""

    with _hold_device_slot("PE1"):
        with pytest.raises(admission.AdmissionDenied) as excinfo:
            with admission.admit("PE1"):
                pass  # pragma: no cover -- must never be reached.
    assert excinfo.value.refusal.scope == "device"
    assert "PE1" in excinfo.value.refusal.reason


def test_admit_refusal_never_blocks_a_different_device():
    """The per-device cap is per-device, not global -- a busy PE1 must never
    refuse a concurrent collection against PE2."""

    with _hold_device_slot("PE1"):
        with admission.admit("PE2"):
            pass  # admitted -- a different device is untouched by PE1's slot.


def test_admit_releases_its_slot_so_a_later_admission_succeeds():
    """The slot is not a one-way valve: once the holder's `with` block exits,
    a later request against the same device is admitted again."""

    with admission.admit("PE1"):
        pass
    with admission.admit("PE1"):
        pass  # would raise AdmissionDenied if the first slot leaked.


def test_admit_raising_inside_the_block_still_releases_the_device_slot():
    """A collection that raises (e.g. a real transport exception) must not
    leave its slot permanently held -- `admit()` is a context manager
    specifically so `__exit__` runs on the exception path too."""

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        with admission.admit("PE1"):
            raise _Boom("transport blew up")

    with admission.admit("PE1"):
        pass  # would raise AdmissionDenied if the crashed attempt leaked its slot.


def test_max_concurrent_per_device_is_configurable(monkeypatch):
    """NETTOOLS_MAX_CONCURRENT_PER_DEVICE raised to 2 admits a second
    concurrent collection that the default (1) would have refused."""

    monkeypatch.setenv("NETTOOLS_MAX_CONCURRENT_PER_DEVICE", "2")
    with _hold_device_slot("PE1", slot=0):
        with admission.admit("PE1"):  # takes slot 1 -- admitted under the raised cap.
            pass


# --------------------------------------------------------------------------- #
# admission.admit(): the fabric-wide cap, independent of the per-device one.
# --------------------------------------------------------------------------- #


def test_admit_refuses_when_the_fabric_wide_cap_is_exhausted(monkeypatch):
    """Even against a device with its OWN slot free, admission is refused
    once every fabric-wide slot is held by other (different-device)
    collections -- the second, independent ceiling B-444 asks for."""

    monkeypatch.setenv("NETTOOLS_MAX_CONCURRENT_FABRIC", "1")
    with _hold_fabric_slot(0):
        with pytest.raises(admission.AdmissionDenied) as excinfo:
            with admission.admit("PE1"):
                pass  # pragma: no cover
    assert excinfo.value.refusal.scope == "fabric"


def test_fabric_refusal_releases_the_device_slot_it_had_already_taken(monkeypatch):
    """A device-level slot taken en route to a fabric-level refusal must not
    leak: the device must be admittable again immediately afterward."""

    monkeypatch.setenv("NETTOOLS_MAX_CONCURRENT_FABRIC", "1")
    with _hold_fabric_slot(0):
        with pytest.raises(admission.AdmissionDenied):
            with admission.admit("PE1"):
                pass  # pragma: no cover

    # The fabric slot above is still held (we are still inside the `with
    # _hold_fabric_slot` block would have released it -- here it is released,
    # by construction of the `with` above already exiting), so a fresh
    # admission against PE1 must succeed cleanly with nothing left over from
    # the refused attempt.
    with admission.admit("PE1"):
        pass


def test_default_fabric_cap_admits_four_concurrent_different_devices(monkeypatch):
    """The measured, documented default (4) admits exactly that many
    concurrent different-device collections with nothing pre-held."""

    monkeypatch.delenv("NETTOOLS_MAX_CONCURRENT_FABRIC", raising=False)
    from contextlib import ExitStack

    with ExitStack() as stack:
        for name in ("PE1", "PE2", "PE3", "PE4"):
            stack.enter_context(admission.admit(name))
        # A fifth concurrent device is refused: every fabric slot is held.
        with pytest.raises(admission.AdmissionDenied) as excinfo:
            with admission.admit("RR1"):
                pass  # pragma: no cover
        assert excinfo.value.refusal.scope == "fabric"


# --------------------------------------------------------------------------- #
# Fail-open: a broken lock directory admits rather than blocking everything.
# --------------------------------------------------------------------------- #


def test_unusable_admission_directory_fails_open_not_closed(monkeypatch, tmp_path, capsys):
    """Admission control is resource protection, not the safety boundary
    (platforms.APPROVED_COMMANDS is, and is untouched by this module's
    existence). A directory that cannot be created must not turn into
    "nothing can ever be collected" -- see admission.py's module docstring,
    "Fail-open, not fail-closed"."""

    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupies the path a directory would need")
    monkeypatch.setenv("NETTOOLS_ADMISSION_DIR", str(blocked / "admission"))

    with admission.admit("PE1"):
        pass  # admitted despite the unusable directory.

    assert "WARNING" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# check_probe_budget(): B-408's rate limit, independent of the concurrency cap.
# --------------------------------------------------------------------------- #


def test_check_probe_budget_admits_up_to_the_per_device_limit(monkeypatch):
    monkeypatch.setenv("NETTOOLS_MAX_ACTIVE_PROBES_PER_DEVICE", "3")
    monkeypatch.setenv("NETTOOLS_MAX_ACTIVE_PROBES_FABRIC", "100")
    for _ in range(3):
        admission.check_probe_budget("PE1")  # no exception -- positive control.


def test_check_probe_budget_refuses_the_one_past_the_per_device_limit(monkeypatch):
    monkeypatch.setenv("NETTOOLS_MAX_ACTIVE_PROBES_PER_DEVICE", "3")
    monkeypatch.setenv("NETTOOLS_MAX_ACTIVE_PROBES_FABRIC", "100")
    for _ in range(3):
        admission.check_probe_budget("PE1")
    with pytest.raises(admission.AdmissionDenied) as excinfo:
        admission.check_probe_budget("PE1")
    assert excinfo.value.refusal.scope == "probe_device"


def test_check_probe_budget_device_limit_is_independent_per_device(monkeypatch):
    monkeypatch.setenv("NETTOOLS_MAX_ACTIVE_PROBES_PER_DEVICE", "1")
    monkeypatch.setenv("NETTOOLS_MAX_ACTIVE_PROBES_FABRIC", "100")
    admission.check_probe_budget("PE1")
    admission.check_probe_budget("PE2")  # a different device's own budget.


def test_check_probe_budget_refuses_at_the_fabric_wide_limit(monkeypatch):
    monkeypatch.setenv("NETTOOLS_MAX_ACTIVE_PROBES_PER_DEVICE", "100")
    monkeypatch.setenv("NETTOOLS_MAX_ACTIVE_PROBES_FABRIC", "2")
    admission.check_probe_budget("PE1")
    admission.check_probe_budget("PE2")
    with pytest.raises(admission.AdmissionDenied) as excinfo:
        admission.check_probe_budget("PE3")
    assert excinfo.value.refusal.scope == "probe_fabric"


def test_check_probe_budget_window_expiry_readmits(monkeypatch):
    """A budget is a RATE, not a lifetime ban -- once the window passes, the
    same device is admitted again."""

    monkeypatch.setenv("NETTOOLS_MAX_ACTIVE_PROBES_PER_DEVICE", "1")
    monkeypatch.setenv("NETTOOLS_MAX_ACTIVE_PROBES_FABRIC", "100")
    monkeypatch.setenv("NETTOOLS_ACTIVE_PROBE_WINDOW_SECONDS", "0.05")
    admission.check_probe_budget("PE1")
    with pytest.raises(admission.AdmissionDenied):
        admission.check_probe_budget("PE1")
    time.sleep(0.1)
    admission.check_probe_budget("PE1")  # window elapsed -- admitted again.


# --------------------------------------------------------------------------- #
# Wiring: network_tools.py's real (non-sender) call sites never return a
# healthy-looking envelope on a refusal, and never even attempt a connection.
# --------------------------------------------------------------------------- #


def test_collect_evidence_refusal_never_opens_a_session_and_never_looks_healthy(
    monkeypatch,
):
    """The B-444 non-negotiable, pinned end to end: a refused collection
    (a) opens zero SSH sessions and (b) reports every intent as an error
    carrying an `admission` marker -- never `status: "success"` with data
    that looks like a genuinely empty, healthy device.
    """

    set_device_environment(monkeypatch)
    sessions = install_fake_netmiko(monkeypatch)

    with _hold_device_slot("PE1"):
        evidence = collect_evidence("PE1")

    assert sessions == [], "a refused collection must never open a connection"
    intent_sections = [v for k, v in evidence.items() if isinstance(v, dict) and "status" in v]
    assert intent_sections, "collect_evidence must still report a section per intent"
    for section in intent_sections:
        assert section["status"] == STATUS_ERROR
        assert section["status"] != STATUS_SUCCESS
        assert section.get("admission", {}).get("outcome") == "refused"
        assert section["admission"]["scope"] == "device"
        assert section["admission"]["reason"]  # never empty (OBS-181-adjacent: always a reason)


def test_collect_evidence_positive_control_succeeds_when_not_refused(monkeypatch):
    """The positive control for the test above: with nothing pre-held, the
    exact same call succeeds and opens exactly one session (the existing
    one-session-per-device batching, unaffected by admission control)."""

    set_device_environment(monkeypatch)
    sessions = install_fake_netmiko(monkeypatch)

    evidence = collect_evidence("PE1")

    assert len(sessions) == 1
    facts_section = evidence.get("facts")
    assert facts_section is not None
    assert facts_section["status"] == STATUS_SUCCESS
    assert "admission" not in facts_section


def test_sender_path_is_never_admission_gated(monkeypatch):
    """A `sender=` short-circuits the transport entirely (fixture replay, a
    test double) -- generates no real traffic, so it must never be refused
    by admission control, even while the device's real slot is held by
    something else. Matches the existing "no device access on the injection
    path" principle (OBS-072) applied to B-444's new gate."""

    def sender(device, command):
        return f"output for {command}"

    with _hold_device_slot("PE1"):
        evidence = collect_evidence("PE1", sender=sender)

    facts_section = evidence.get("facts")
    assert facts_section is not None
    assert facts_section["status"] == STATUS_SUCCESS
    assert "admission" not in facts_section


def test_ping_refusal_never_opens_a_session(monkeypatch):
    """B-408's wiring, end to end: an over-budget probe never reaches the
    transport, and the resulting envelope is a refusal, never a healthy
    result claiming the ping ran."""

    set_device_environment(monkeypatch)
    sessions = install_fake_netmiko(monkeypatch)
    monkeypatch.setenv("NETTOOLS_MAX_ACTIVE_PROBES_PER_DEVICE", "1")
    monkeypatch.setenv("NETTOOLS_MAX_ACTIVE_PROBES_FABRIC", "100")

    first = ping_device("PE1", "10.255.0.31")
    assert first["status"] == STATUS_SUCCESS
    assert len(sessions) == 1

    second = ping_device("PE1", "10.255.0.31")
    assert second["status"] == STATUS_ERROR
    assert second["status"] != STATUS_SUCCESS
    assert second["admission"]["scope"] == "probe_device"
    assert len(sessions) == 1, "the refused probe must never open a second session"


def test_ping_with_sender_is_never_probe_budget_gated(monkeypatch):
    """Same exemption as the concurrency gate: a fixture-backed ping
    generates no real traffic and must never consume or be refused by the
    probe budget."""

    monkeypatch.setenv("NETTOOLS_MAX_ACTIVE_PROBES_PER_DEVICE", "1")
    monkeypatch.setenv("NETTOOLS_MAX_ACTIVE_PROBES_FABRIC", "1")

    def sender(device, command):
        return "5 packets transmitted, 5 received"

    for _ in range(5):
        result = ping_device("PE1", "10.255.0.31", sender=sender)
        assert result["status"] == STATUS_SUCCESS


# --------------------------------------------------------------------------- #
# admission.AdmissionRefusal: reason is never empty (mirrors
# checks.unevaluated's own "reason always populated" discipline).
# --------------------------------------------------------------------------- #


def test_admission_refusal_always_carries_a_populated_reason():
    with _hold_device_slot("PE1"):
        with pytest.raises(admission.AdmissionDenied) as excinfo:
            with admission.admit("PE1"):
                pass  # pragma: no cover
    assert excinfo.value.refusal.reason.strip() != ""
    assert str(excinfo.value).strip() != ""
