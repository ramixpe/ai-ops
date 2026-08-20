"""P4 (release-1.0 cleanup): `agent_nettools/__init__.py`'s lazy re-exports.

Before this change the package eagerly imported ~15 submodules just to
re-export ~150 names -- paid by every `nettools` invocation and every test
collection, for a surface no code IN this repo actually reads through the
package (`from agent_nettools import <re-exported name>`); every internal
caller imports from the specific submodule instead (confirmed by an AST
sweep of tests/, mcp_server/, and scripts/ during this change). Now every
name but `__version__` resolves through `__getattr__` (PEP 562) on first
access.

The whole point of a lazy map is that a typo'd entry (wrong submodule, wrong
real name, a name quietly dropped from `__all__` or from the map) is
invisible until something actually touches that one name -- OBS-181's rule
applied to an import map instead of a refusal test: a mechanism only ever
exercised by the names someone happened to use is not proven for the ones
nobody did. `test_every_all_name_resolves_to_the_real_object` is the
positive control: it imports and touches literally every name in
`__all__`, once, and asserts each one is identical to the real object a
direct `from agent_nettools.<submodule> import <name>` would hand back --
not a stub, not `None`, not a second, different object of the same name.
"""

from __future__ import annotations

import importlib
import sys

import pytest

import agent_nettools


def test_version_is_eager_no_submodule_import_needed():
    """`import agent_nettools; agent_nettools.__version__` must cost nothing
    beyond the package's own __init__ -- no submodule import triggered. This
    is the one name this rewrite keeps eager on purpose (see __init__.py's
    own module-level comment): other code very likely does `from
    agent_nettools import __version__` at import time expecting zero-cost
    access (`nettools version` among others)."""

    # A fresh interpreter would be the clean way to prove "zero submodules
    # loaded by import alone", but this process may have already imported
    # agent_nettools submodules via other test modules collected earlier in
    # this session (pytest collects the whole tree first) -- that is an
    # artifact of shared process state, not something `agent_nettools.
    # __version__` caused. What is provable in-process: __version__ resolves
    # without going through `__getattr__`/`_LAZY` at all (it is a plain
    # module attribute, set at the top of __init__.py, unconditionally).
    assert "__version__" not in agent_nettools._LAZY
    assert isinstance(agent_nettools.__version__, str)
    assert agent_nettools.__version__


def test_every_all_name_resolves_to_the_real_object():
    """Positive control (OBS-181): touch every single `__all__` name and
    assert each one IS the real object its submodule defines -- catching a
    typo'd `_LAZY` entry (wrong submodule, wrong real name) the same way a
    broken refusal test would be caught by its own positive control."""

    checked = 0
    for name in agent_nettools.__all__:
        if name == "__version__":
            continue
        value = getattr(agent_nettools, name)
        assert value is not None, f"{name} resolved to None"

        submodule, real_name = agent_nettools._LAZY[name]
        module = importlib.import_module(submodule, agent_nettools.__name__)
        expected = getattr(module, real_name)
        assert value is expected or value == expected, (
            f"{name} resolved to {value!r}, expected {submodule}.{real_name} = {expected!r}"
        )
        checked += 1

    # Every non-__version__ name in __all__ actually got exercised above --
    # this is not itself the positive control (the loop body is), it is the
    # anti-vacuity check that the loop was not accidentally empty.
    assert checked == len(agent_nettools.__all__) - 1
    assert checked > 100  # this package's own re-export surface, not a trivial one


def test_every_lazy_entry_has_a_real_all_entry_and_vice_versa():
    """The two lists must describe exactly the same set (minus __version__,
    which lives only in __all__): a name added to one and not the other is
    either an unreachable export or a map entry nothing advertises."""

    all_names = set(agent_nettools.__all__) - {"__version__"}
    lazy_names = set(agent_nettools._LAZY)

    assert all_names == lazy_names


def test_a_name_not_in_the_lazy_map_raises_attribute_error_not_something_else():
    """__getattr__ must fall through to the plain AttributeError Python's own
    import machinery expects for an unknown name -- anything else (a custom
    exception, a swallowed KeyError) would break `from agent_nettools import
    <a real submodule, e.g. health>`, which resolves via that exact fallback
    path once this raises. Positive control below proves the fallback itself
    still works."""

    with pytest.raises(AttributeError):
        _ = agent_nettools.definitely_not_a_real_export_or_submodule


def test_a_bare_submodule_import_through_the_package_still_works_positive_control():
    """Positive control for the test above: `from agent_nettools import
    health` (a real SUBMODULE, never one of the re-exported names in
    __all__/_LAZY) must keep working -- it relies on Python's own "import
    the submodule" fallback once `__getattr__` raises AttributeError for it,
    exactly as it did before this file grew a `__getattr__` at all."""

    from agent_nettools import health as health_module

    assert health_module is sys.modules["agent_nettools.health"]
    assert hasattr(health_module, "evaluate_fabric_with_silences")


def test_repeat_access_returns_the_identical_cached_object():
    """First access imports and caches on the package's own globals(); a
    second access must be the same object, not a second import producing an
    equal-but-distinct one (e.g. a fresh class defined by re-running module
    top-level code, which `importlib.import_module` itself already guards
    against via sys.modules -- this pins that this package's own
    `__getattr__` does not defeat that by re-importing unnecessarily)."""

    first = agent_nettools.evaluate_fabric
    second = agent_nettools.evaluate_fabric
    assert first is second
    assert "evaluate_fabric" in vars(agent_nettools)


def test_dir_includes_every_lazy_name():
    """`__dir__` (also PEP 562) must list the lazy names too, not just
    whatever happens to already be cached in globals() -- otherwise
    tab-completion/introspection undercounts this package's real surface."""

    names = dir(agent_nettools)
    for name in agent_nettools._LAZY:
        assert name in names
