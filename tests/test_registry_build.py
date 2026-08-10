"""The published artifact is the contract between this repo and the engine.

Layout: an index plus one file per contract. What these tests hold to is the part
the engine depends on -- that every contract listed is present, that its recorded
sha256 matches its bytes, and that the provenance the engine pins is there.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def build(tmp_path: Path) -> tuple[dict, Path]:
    out = tmp_path / "registry"
    result = subprocess.run(
        [sys.executable, "scripts/build_registry.py", "--out", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads((out / "registry.json").read_text(encoding="utf-8")), out


def test_every_approved_contract_gets_its_own_file(tmp_path):
    """One file per contract is what keeps the engine's pin PR reviewable."""
    index, out = build(tmp_path)
    on_disk = sorted(p.name for p in (REPO_ROOT / "contracts").rglob("*.json"))

    assert len(index["contracts"]) == len(on_disk)
    for entry in index["contracts"]:
        assert (out / entry["path"]).is_file(), entry["path"]
        assert entry["path"] == f"contracts/{entry['name']}.json"


def test_every_recorded_hash_matches_its_file(tmp_path):
    """The engine refuses a contract whose bytes disagree with the index.

    So an artifact whose own hashes are wrong would be dead on arrival -- which is
    exactly what happened when the builder wrote text on Windows and hashed LF.
    """
    index, out = build(tmp_path)

    for entry in index["contracts"]:
        actual = hashlib.sha256((out / entry["path"]).read_bytes()).hexdigest()
        assert actual == entry["sha256"], entry["name"]


def test_a_rebuild_is_byte_identical(tmp_path):
    """Lets the engine's consume step say "nothing changed" and mean it."""
    first, out_a = build(tmp_path / "a")
    second, out_b = build(tmp_path / "b")

    assert first["contracts"] == second["contracts"]
    for entry in first["contracts"]:
        assert (out_a / entry["path"]).read_bytes() == (out_b / entry["path"]).read_bytes()


def test_the_index_carries_the_provenance_the_engine_pins(tmp_path):
    index, _ = build(tmp_path)

    assert index["sourceRepo"] == "cm_mcp_contracts"
    assert index["generatedAt"]
    assert index["schemaId"]
    assert index["layout"] == "index"
    assert index["toolCount"] == len(index["toolNames"]) == len(index["contracts"])


def test_no_contract_body_is_inlined_in_the_index(tmp_path):
    """The index points; it does not carry. At 500 tools that is the difference."""
    index, _ = build(tmp_path)

    for entry in index["contracts"]:
        assert set(entry) == {"name", "version", "path", "sha256"}


def test_tool_names_are_read_from_the_interface(tmp_path):
    """toolNames is what the engine checks before pinning a registry.

    Built from a contract this test writes rather than whichever seeds happen to be
    approved, so it keeps working as the lane's contents change.
    """
    from tests.test_salla_rules import READ_CONTRACT

    path = REPO_ROOT / "contracts" / "_tmp_single_for_test.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(READ_CONTRACT), encoding="utf-8")
    try:
        index, out = build(tmp_path)
    finally:
        path.unlink()

    assert "list_coupons" in index["toolNames"]
    assert (out / "contracts" / "list_coupons.json").is_file()


def test_build_refuses_an_invalid_contract(tmp_path):
    """A registry that exists is a registry that passed the gate."""
    broken = REPO_ROOT / "contracts" / "_tmp_broken_for_test.json"
    broken.parent.mkdir(exist_ok=True)
    broken.write_text(json.dumps({"contractVersion": "1.0.0", "kind": "single-tool"}), "utf-8")
    try:
        result = subprocess.run(
            [sys.executable, "scripts/build_registry.py", "--out", str(tmp_path / "reg")],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "Refusing to publish" in result.stdout
    finally:
        broken.unlink()


def test_a_deleted_contract_disappears_from_a_rebuild(tmp_path):
    """The output directory is rebuilt, not updated in place.

    A leftover file would be served by an engine that trusted the directory -- and
    would not be listed in the index, so it would at least be refused loudly. Better
    that it is simply gone.
    """
    extra = REPO_ROOT / "contracts" / "_tmp_temporary_for_test.json"
    from tests.test_salla_rules import READ_CONTRACT

    extra.write_text(json.dumps(READ_CONTRACT), encoding="utf-8")
    out = tmp_path / "registry"
    try:
        build_result = subprocess.run(
            [sys.executable, "scripts/build_registry.py", "--out", str(out)],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert build_result.returncode == 0, build_result.stdout
        assert (out / "contracts" / "list_coupons.json").is_file()
    finally:
        extra.unlink()

    # Same output directory, contract gone upstream.
    subprocess.run(
        [sys.executable, "scripts/build_registry.py", "--out", str(out)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    assert not (out / "contracts" / "list_coupons.json").exists()
