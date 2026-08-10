"""The governance gate: approved contracts pass, broken ones are rejected."""

import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SCHEMA_PATH = REPO_ROOT / "schema" / "tool-contract.v1.json"
CONTRACTS_DIR = REPO_ROOT / "contracts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_INVALID = Path(__file__).parent / "fixtures" / "invalid"

APPROVED = sorted(CONTRACTS_DIR.rglob("*.json"))
INVALID = sorted(FIXTURES_INVALID.glob("*.json"))
TEMPLATES = sorted(TEMPLATES_DIR.glob("*.template.json"))


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def test_the_schema_itself_is_a_valid_json_schema(validator):
    Draft202012Validator.check_schema(validator.schema)


@pytest.mark.parametrize("path", APPROVED, ids=lambda p: p.name)
def test_approved_contracts_validate(path, validator):
    contract = json.loads(path.read_text(encoding="utf-8"))
    errors = sorted(validator.iter_errors(contract), key=str)
    assert not errors, f"{path.name}: {[e.message for e in errors]}"


@pytest.mark.parametrize("path", APPROVED, ids=lambda p: p.name)
def test_approved_contracts_reference_the_schema(path):
    """The $schema key is what gives contributors editor validation before CI."""
    contract = json.loads(path.read_text(encoding="utf-8"))
    assert contract.get("$schema") == "../schema/tool-contract.v1.json"


def test_tool_names_are_unique_across_the_registry():
    """A duplicate name silently shadows a partner's tool at load time."""
    seen: dict[str, str] = {}
    for path in APPROVED:
        contract = json.loads(path.read_text(encoding="utf-8"))
        bodies = (
            contract.get("tools", [])
            if contract.get("kind") == "multi-tool"
            else [contract]
        )
        for body in bodies:
            name = body.get("interface", {}).get("name")
            if not name:
                continue
            assert name not in seen, f"{name!r} defined in both {seen[name]} and {path.name}"
            seen[name] = path.name


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_broken_contracts_are_rejected(path, validator):
    contract = json.loads(path.read_text(encoding="utf-8"))
    assert list(validator.iter_errors(contract)), f"{path.name} should not have validated"


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_rejections_name_the_offending_field(path, validator):
    """A rejection the contributor cannot act on is barely better than none."""
    from scripts.validate_contracts import validate_file

    problems = validate_file(path, validator)
    assert problems

    for problem in problems:
        assert ": " in problem, problem
        # The binding oneOf must be resolved to the branch the author meant,
        # never dumped as an undifferentiated pile of every branch's complaints.
        assert "is not valid under any of the given schemas" not in problem, problem


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_templates_point_at_the_schema(path):
    template = json.loads(path.read_text(encoding="utf-8"))
    assert template["$schema"] == "../schema/tool-contract.v1.json"


# What a contributor types over the placeholders. Order matters: read_write must
# be replaced before read, or the longer placeholder is corrupted.
TEMPLATE_FILLS = {
    "TODO_snake_case_name": "get_coupon",
    "TODO_domain.read_write": "coupons.read_write",
    "TODO_domain.read": "coupons.read",
    "TODO-Operation-Id": "Coupon-Details",
}


def _fill(node):
    """Do what a contributor does: replace the TODO placeholders."""
    if isinstance(node, dict):
        return {k: _fill(v) for k, v in node.items() if not k.startswith("_comment")}
    if isinstance(node, list):
        return [_fill(v) for v in node]
    if isinstance(node, str):
        for placeholder, value in TEMPLATE_FILLS.items():
            node = node.replace(placeholder, value)
        return node
    return node


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_a_filled_in_template_validates(path, validator):
    """A partner copies a template, fills it, and it passes. If a template
    cannot become a valid contract by filling in its blanks, it is not
    scaffolding -- it is a trap."""
    filled = _fill(json.loads(path.read_text(encoding="utf-8")))
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}"
        for e in validator.iter_errors(filled)
    ]
    assert not errors, f"{path.name} cannot be filled into a valid contract: {errors[:4]}"


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_comment_keys_are_allowed_by_the_schema(path, validator):
    """The schema permits ^_comment anywhere, so keeping the hints must not be
    what breaks a contributor's first submission."""
    with_comments = {
        **_fill(json.loads(path.read_text(encoding="utf-8"))),
        "_comment": "a contributor left their notes in",
        "_comment_extra": "and a second one",
    }
    assert not list(validator.iter_errors(with_comments))


def test_removed_kinds_point_at_future_work(tmp_path, validator):
    """multi-tool and openapi-import were cut from v1 to keep the rulebook small.

    Someone submitting one should learn where those kinds went, not just that
    'single-tool' was expected.
    """
    import copy

    from scripts.validate_contracts import validate_file
    from tests.test_salla_rules import READ_CONTRACT

    for removed in ("multi-tool", "openapi-import"):
        contract = copy.deepcopy(READ_CONTRACT)
        contract["kind"] = removed
        path = tmp_path / f"{removed}.json"
        path.write_text(json.dumps(contract), encoding="utf-8")

        problems = validate_file(path, validator)
        assert problems, f"{removed} should not validate"
        assert any("future work" in line for line in problems), problems
