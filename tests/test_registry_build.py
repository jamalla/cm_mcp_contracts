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


def test_tool_names_are_read_from_the_interface(tmp_path):
    """toolNames is what the engine checks before pinning a registry.

    Built from a contract this test writes rather than whichever seeds happen
    to be approved, so it keeps working as the lane's contents change.
    """
    from tests.test_salla_rules import READ_CONTRACT

    path = REPO_ROOT / "contracts" / "_tmp_single_for_test.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(READ_CONTRACT), encoding="utf-8")
    try:
        payload = build(tmp_path)
    finally:
        path.unlink()

    assert "list_coupons" in payload["toolNames"]
    assert payload["toolCount"] == len(payload["toolNames"])


def test_build_refuses_an_invalid_contract(tmp_path):
    """A registry that exists is a registry that passed the gate."""
    broken = REPO_ROOT / "contracts" / "_tmp_broken_for_test.json"
    broken.parent.mkdir(exist_ok=True)
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
