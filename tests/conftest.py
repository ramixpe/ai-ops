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
