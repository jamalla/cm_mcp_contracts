"""The published artifact is the contract between this repo and the engine."""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def build(tmp_path: Path) -> dict:
    out = tmp_path / "registry.generated.json"
    result = subprocess.run(
        [sys.executable, "scripts/build_registry.py", "--out", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def test_registry_contains_every_approved_contract(tmp_path):
    payload = build(tmp_path)
    on_disk = sorted(p.name for p in (REPO_ROOT / "contracts").rglob("*.json"))
    assert len(payload["contracts"]) == len(on_disk)


def test_registry_carries_the_provenance_the_engine_pins(tmp_path):
    payload = build(tmp_path)
    assert payload["sourceRepo"] == "cm_mcp_contracts"
    assert payload["generatedAt"]
    assert payload["schemaId"]
    assert payload["toolCount"] == len(payload["toolNames"])


def test_multi_tool_packages_are_expanded_in_the_name_list(tmp_path):
    """A consumer deciding whether it can serve this registry needs every tool
    name, including the ones bundled inside a package.

    Built from a contract this test writes rather than whichever seeds happen to
    be approved, so it keeps testing expansion as the registry's contents change.
    """
    from tests.test_salla_rules import DELETE_CONTRACT, READ_CONTRACT

    package = {
        "$schema": "../schema/tool-contract.v1.json",
        "contractVersion": "1.0.0",
        "kind": "multi-tool",
        "package": {
            "name": "temp_multi_for_test",
            "description": "Temporary package used to prove multi-tool expansion.",
        },
        "tools": [
            {k: v for k, v in READ_CONTRACT.items() if k in {"interface", "binding", "governance"}},
            {k: v for k, v in DELETE_CONTRACT.items() if k in {"interface", "binding", "governance"}},
        ],
    }

    path = REPO_ROOT / "contracts" / "_tmp_multi_for_test.json"
    path.write_text(json.dumps(package), encoding="utf-8")
    try:
        payload = build(tmp_path)
    finally:
        path.unlink()

    assert "list_coupons" in payload["toolNames"]
    assert "delete_coupon" in payload["toolNames"]
    assert "temp_multi_for_test" not in payload["toolNames"], "the package is not itself a tool"


def test_build_refuses_an_invalid_contract(tmp_path):
    """A registry that exists is a registry that passed the gate."""
    broken = REPO_ROOT / "contracts" / "_tmp_broken_for_test.json"
    broken.write_text(json.dumps({"contractVersion": "1.0.0", "kind": "single-tool"}), "utf-8")
    try:
        result = subprocess.run(
            [sys.executable, "scripts/build_registry.py", "--out", str(tmp_path / "r.json")],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "Refusing to publish" in result.stdout
    finally:
        broken.unlink()
