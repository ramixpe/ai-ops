"""Test-suite-wide isolation.

Nothing here changes behaviour under test; it stops the suite writing into the
working tree.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _tickets_never_land_in_the_repo(tmp_path, monkeypatch):
    """Point every test's flight-recorder tickets at a temp directory.

    `nettools investigate` opens a ticket (B-446) and defaults to `tickets/`
    beside the repo. Any test that runs the real CLI therefore wrote a ticket
    into the working tree, and 113 of them were committed by a `git add -A`
    before anyone noticed (OBS-172).

    Autouse and unconditional: a test that WANTS a specific directory overrides
    the variable itself, and a test that does not think about tickets at all
    still cannot leave one behind. Opt-out isolation is isolation nobody
    forgets.
    """

    monkeypatch.setenv("NETTOOLS_TICKET_DIR", str(tmp_path / "tickets"))


@pytest.fixture(autouse=True)
def _admission_state_never_leaks_across_tests(tmp_path, monkeypatch):
    """Point every test's admission/probe-budget lock files at a temp directory.

    admission.py (B-408/B-444) gates concurrent collection and active-probe
    rate budgeting with real flock files under `NETTOOLS_ADMISSION_DIR`
    (default `./admission`) -- deliberately filesystem-based and cross-process,
    since a routed investigation is a separate OS process (event_routing.py).
    Left at its default, every test that exercises the real (non-`sender`)
    transport path -- `install_fake_netmiko`, which still runs the real
    `_netmiko_send_commands` this module gates -- would read and write the
    SAME `./admission` directory as every other test in the same run, and the
    probe-budget windows in particular persist actual timestamps on disk: a
    handful of ping/traceroute tests sharing one device name is enough to
    silently exhaust `NETTOOLS_MAX_ACTIVE_PROBES_PER_DEVICE` partway through
    an unrelated later test, the same "writes into the working tree and
    contaminates the next thing that reads it" shape `NETTOOLS_TICKET_DIR`
    above already exists to close (OBS-172). Same fix, same reasoning: give
    every test its own directory nobody else can see, autouse and unconditional.
    """

    monkeypatch.setenv("NETTOOLS_ADMISSION_DIR", str(tmp_path / "admission"))


@pytest.fixture(autouse=True)
def _session_memory_never_lands_in_the_repo(tmp_path, monkeypatch):
    """Point every test's session-memory pointer files at a temp directory.

    `nettools investigate` records a session turn (B-407) after every run and
    defaults to `session_memory/` beside the repo -- the identical shape
    `NETTOOLS_TICKET_DIR` above exists to close (OBS-172), one file per
    session id (typically a parent process id) rather than one per ticket,
    but landing in the working tree either way. Same fix, same reasoning.
    """

    monkeypatch.setenv("NETTOOLS_SESSION_MEMORY_DIR", str(tmp_path / "session_memory"))


@pytest.fixture(autouse=True)
def _retries_never_sleep(monkeypatch):
    """Zero the retry backoff for every test.

    `_netmiko_send_commands` retries transient transport failures with
    exponential backoff (default 0.5s, doubling). Every test that exercises a
    failing transport through the real retry path therefore slept real
    wall-clock time proving something no assertion cared about -- measured at
    ~3.5s across the suite (P2, release-1.0 campaign). Three tests in
    `test_network_tools.py` already zeroed it by hand; now the suite default
    is zero and a test that wants REAL backoff timing opts back in by setting
    `NETTOOLS_RETRY_BACKOFF_SECONDS` itself -- same opt-out-is-not-isolation
    reasoning as the ticket fixture above. No test pins the 0.5 default
    through the env path (checked before this landed).
    """

    monkeypatch.setenv("NETTOOLS_RETRY_BACKOFF_SECONDS", "0")
