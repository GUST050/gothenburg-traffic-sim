"""TraCI discovery (LUNA-WARM-15).

WHY THIS SUITE EXISTS. The v6 campaign failed on all three identities with
`No module named 'traci'`: production did a bare `import traci`, but the package
ships inside the SUMO installation at `<sumo_home>/tools/traci` and is not on
`sys.path`. Warming had never once started.

Every test that touched the controller injected a fake traci module, so the one
line that actually mattered — the real resolution — was never executed by
anything but an approved campaign. A dependency a test SUPPLIES is a dependency
that test cannot check. So these tests drive Python's real import machinery
against a temporary fake SUMO tree, and would fail against v6's bare import.

Process-free: no installed simulator, no socket, no subprocess, no `runs/`
access. `sys.path` and `sys.modules` effects are isolated per test.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from traffic_sim.simulation.runtime import (
    REQUIRED_TRACI_API, REQUIRED_TRACI_NAMESPACES, SumoRuntimeError,
    resolve_traci, sumo_home, traci_tools_dir, validate_traci_api)


# ── A fake SUMO installation, built on disk ─────────────────────────────────

def _fake_home(tmp_path, *, api=True, package=True, tools=True,
               name="traci") -> Path:
    """A SUMO home whose TraCI is a REAL importable package.

    Real, not a mock: the point is to exercise the import machinery, which a
    stubbed module would bypass entirely.
    """
    home = tmp_path / "sumo"
    if tools:
        (home / "tools").mkdir(parents=True)
    else:
        home.mkdir(parents=True)
    if package:
        pkg = home / "tools" / name
        pkg.mkdir(parents=True)
        body = ['"""A stand-in TraCI package."""']
        if api:
            body += [
                "def init(port=None): raise AssertionError('never called')",
                "def close(): raise AssertionError('never called')",
                "def simulationStep(t=None): raise AssertionError('never called')",
                "class simulation:",
                "    @staticmethod",
                "    def getDeltaT(): raise AssertionError('never called')",
                "    @staticmethod",
                "    def getTime(): raise AssertionError('never called')",
                "    @staticmethod",
                "    def saveState(path): raise AssertionError('never called')",
                "class vehicle:",
                "    @staticmethod",
                "    def getIDList(): raise AssertionError('never called')",
            ]
        (pkg / "__init__.py").write_text("\n".join(body) + "\n")
    return home


@pytest.fixture
def isolated():
    """Run the REAL import machinery, then put the interpreter back.

    The resolver must use the real `sys.path` — that is the whole point, and an
    injected path list would not be consulted by `import_module` at all. So
    isolation belongs here: snapshot the interpreter state, let the test import
    for real, and restore afterwards.
    """
    before_path, before_modules = list(sys.path), dict(sys.modules)
    registry: dict = {}
    yield {"modules": registry}
    sys.path[:] = before_path
    for name in set(sys.modules) - set(before_modules):
        del sys.modules[name]


# ── sumo_home ───────────────────────────────────────────────────────────────

class TestSumoHome:

    def test_a_declared_home_wins(self, tmp_path):
        home = _fake_home(tmp_path)
        assert sumo_home({"SUMO_HOME": str(home)}) == home

    def test_surrounding_whitespace_is_ignored(self, tmp_path):
        home = _fake_home(tmp_path)
        assert sumo_home({"SUMO_HOME": f"  {home}  "}) == home

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_an_empty_declaration_falls_through_to_the_package(self, value):
        """Empty is 'not set', not 'set to nothing'."""
        resolved = sumo_home({} if value is None else {"SUMO_HOME": value})
        assert resolved.is_dir()

    def test_a_declared_home_that_does_not_exist_is_an_error(self, tmp_path):
        """NEVER a silent fallback: resolving TraCI from one installation while
        running binaries from another is the failure this prevents."""
        missing = tmp_path / "nope"
        with pytest.raises(SumoRuntimeError, match="not a directory"):
            sumo_home({"SUMO_HOME": str(missing)})

    def test_a_declared_home_that_is_a_file_is_an_error(self, tmp_path):
        target = tmp_path / "file"
        target.write_text("x")
        with pytest.raises(SumoRuntimeError, match="not a directory"):
            sumo_home({"SUMO_HOME": str(target)})


# ── tools directory ─────────────────────────────────────────────────────────

class TestToolsDir:

    def test_a_complete_installation_resolves(self, tmp_path):
        home = _fake_home(tmp_path)
        assert traci_tools_dir(home) == home / "tools"

    def test_a_missing_tools_directory_is_specific(self, tmp_path):
        home = _fake_home(tmp_path, tools=False, package=False)
        with pytest.raises(SumoRuntimeError, match="no tools"):
            traci_tools_dir(home)

    def test_a_missing_traci_package_is_specific(self, tmp_path):
        home = _fake_home(tmp_path, package=False)
        with pytest.raises(SumoRuntimeError, match="TraCI is not present"):
            traci_tools_dir(home)


# ── the resolver, through real import machinery ─────────────────────────────

class TestResolveTraci:

    def test_it_imports_from_the_given_home(self, tmp_path, isolated):
        home = _fake_home(tmp_path)
        module = resolve_traci(home, **isolated)
        assert Path(module.__file__).resolve().parent == \
            (home / "tools" / "traci").resolve()

    def test_it_would_fail_against_v6s_bare_import(self, tmp_path, isolated):
        """The regression proper: with only the fake home available, a bare
        `import traci` finds nothing — which is exactly what v6 recorded."""
        import importlib
        home = _fake_home(tmp_path)
        assert "traci" not in isolated["modules"]
        with pytest.raises(ModuleNotFoundError):
            # No path insertion: precisely v6's behaviour.
            importlib.import_module("traci_definitely_not_installed")
        # The resolver, given the same environment, succeeds.
        assert resolve_traci(home, **isolated) is not None

    def test_it_inserts_the_tools_directory_on_the_real_path(self, tmp_path,
                                                              isolated):
        """And LEAVES it there: TraCI imports siblings such as `sumolib` from
        the same directory, so removing the entry would break later imports."""
        home = _fake_home(tmp_path)
        resolve_traci(home, **isolated)
        assert str(home / "tools") in sys.path

    def test_it_does_not_insert_the_same_directory_twice(self, tmp_path,
                                                         isolated):
        home = _fake_home(tmp_path)
        resolve_traci(home, **isolated)
        del sys.modules["traci"]
        resolve_traci(home, **isolated)
        assert sys.path.count(str(home / "tools")) == 1

    def test_a_missing_package_fails_before_anything_else(self, tmp_path,
                                                          isolated):
        home = _fake_home(tmp_path, package=False)
        with pytest.raises(SumoRuntimeError, match="TraCI is not present"):
            resolve_traci(home, **isolated)

    def test_a_malformed_package_reports_the_import_error(self, tmp_path,
                                                          isolated):
        home = _fake_home(tmp_path)
        (home / "tools" / "traci" / "__init__.py").write_text(
            "raise RuntimeError('broken package')\n")
        with pytest.raises(SumoRuntimeError, match="could not be imported"):
            resolve_traci(home, **isolated)

    def test_a_module_from_elsewhere_is_refused(self, tmp_path, isolated):
        """A stray `traci` already on the path must not stand in for the SUMO we
        are about to run."""
        other = tmp_path / "elsewhere"
        other.mkdir()
        impostor = other / "impostor.py"
        impostor.write_text("__file__ = 'x'\n")

        class Impostor:
            __file__ = str(impostor)

        isolated["modules"]["traci"] = Impostor()
        with pytest.raises(SumoRuntimeError, match="outside the active"):
            resolve_traci(_fake_home(tmp_path), **isolated)

    def test_a_module_without_an_origin_is_refused(self, tmp_path, isolated):
        class NoOrigin:
            pass

        isolated["modules"]["traci"] = NoOrigin()
        with pytest.raises(SumoRuntimeError, match="no origin"):
            resolve_traci(_fake_home(tmp_path), **isolated)

    def test_an_already_loaded_matching_module_is_reused(self, tmp_path,
                                                         isolated):
        home = _fake_home(tmp_path)
        first = resolve_traci(home, **isolated)
        isolated["modules"]["traci"] = first
        assert resolve_traci(home, **isolated) is first


# ── API validation ──────────────────────────────────────────────────────────

class TestValidateTraciApi:

    def test_a_complete_module_reports_its_origin(self, tmp_path, isolated):
        module = resolve_traci(_fake_home(tmp_path), **isolated)
        report = validate_traci_api(module)
        assert report["api_complete"] is True
        assert report["origin"].endswith("__init__.py")
        assert report["required_api"] == list(REQUIRED_TRACI_API)

    def test_a_module_missing_a_function_is_refused(self, tmp_path, isolated):
        module = resolve_traci(_fake_home(tmp_path), **isolated)
        delattr(module, "simulationStep")
        with pytest.raises(SumoRuntimeError, match="simulationStep"):
            validate_traci_api(module)

    def test_a_module_missing_a_namespace_is_refused(self, tmp_path, isolated):
        module = resolve_traci(_fake_home(tmp_path), **isolated)
        delattr(module, "vehicle")
        with pytest.raises(SumoRuntimeError, match="vehicle"):
            validate_traci_api(module)

    def test_a_namespace_missing_an_attribute_is_refused(self, tmp_path,
                                                         isolated):
        module = resolve_traci(_fake_home(tmp_path), **isolated)
        delattr(module.simulation, "saveState")
        with pytest.raises(SumoRuntimeError, match="simulation.saveState"):
            validate_traci_api(module)

    def test_a_package_without_any_api_is_refused(self, tmp_path, isolated):
        module = resolve_traci(_fake_home(tmp_path, api=False), **isolated)
        with pytest.raises(SumoRuntimeError, match="missing required API"):
            validate_traci_api(module)

    def test_validation_calls_nothing(self, tmp_path, isolated):
        """Every stand-in raises if invoked, so a call would fail loudly."""
        module = resolve_traci(_fake_home(tmp_path), **isolated)
        assert validate_traci_api(module)["api_complete"] is True

    def test_the_required_surface_is_what_production_uses(self):
        source = Path("traffic_sim/simulation/warm_state_boundary.py").read_text()
        for name in REQUIRED_TRACI_API:
            assert f"traci.{name}(" in source, name
        for namespace, attributes in REQUIRED_TRACI_NAMESPACES.items():
            for attribute in attributes:
                assert f"{namespace}.{attribute}(" in source, \
                    f"{namespace}.{attribute}"


# ── Process-free guards ─────────────────────────────────────────────────────

class TestProcessFree:

    def test_no_installed_simulator_is_imported_by_this_suite(self):
        assert "traci" not in sys.modules
        assert "libsumo" not in sys.modules

    def test_the_runtime_module_imports_no_simulator_at_module_scope(self):
        import ast
        tree = ast.parse(Path("traffic_sim/simulation/runtime.py").read_text())
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                top.add(node.module.split(".")[0])
        assert not top & {"traci", "libsumo", "sumo", "socket", "subprocess"}, \
            sorted(top)

    def test_the_runtime_module_names_no_forbidden_location(self):
        import ast
        import io
        import tokenize
        text = Path("traffic_sim/simulation/runtime.py").read_text()
        tree = ast.parse(text)
        docstrings = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                continue
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                docstrings.add((body[0].value.lineno, body[0].value.col_offset))
        kept = []
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                continue
            if token.type == tokenize.STRING and token.start in docstrings:
                continue
            kept.append(token.string)
        assert "run" + "s/" not in "\n".join(kept)

    def test_production_no_longer_contains_a_bare_traci_import(self):
        """The v6 defect itself, asserted gone."""
        import ast
        source = Path("traffic_sim/simulation/warm_state_boundary.py").read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                assert not any(a.name == "traci" for a in node.names), \
                    "a bare `import traci` is back; v6 failed on exactly this"
