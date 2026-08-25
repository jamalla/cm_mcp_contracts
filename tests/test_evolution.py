"""Changing an approved contract, and saying so.

Every other check in this repo asks whether a contract is well formed *now*.
This one is the only place that knows what a contract used to be, which makes it
the only place that can catch the failure of an UPDATE rather than of an
addition: an argument quietly deleted from a tool agents already route to, with
the version left where it was.

The classification is deliberately conservative about major. Each rule below
describes something a caller already depends on -- an argument it sends, a field
it reads, the scopes a merchant consented to, whether the tool runs when asked.
Anything that only adds is minor; wording and rendering are patch.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.check_evolution import (  # noqa: E402
    bump_level,
    problems_for,
    required_level,
)

BASE = json.loads(
    (REPO_ROOT / "contracts" / "list_products.json").read_text(encoding="utf-8")
)


def arguments(contract: dict) -> dict:
    return contract["interface"]["input"]["schema"]["properties"]


def edit(change, version: str | None = None) -> dict:
    contract = copy.deepcopy(BASE)
    change(contract)
    if version is not None:
        contract["contractVersion"] = version
    return contract


def test_an_unchanged_contract_needs_nothing():
    assert required_level(BASE, copy.deepcopy(BASE)) == ("none", [])
    assert problems_for("list_products", BASE, copy.deepcopy(BASE)) == []


@pytest.mark.parametrize(
    ("label", "change"),
    [
        ("an argument removed", lambda c: arguments(c).pop("keyword")),
        (
            "an optional argument made required",
            lambda c: c["interface"]["input"]["schema"]["required"].append("keyword"),
        ),
        ("an enum narrowed", lambda c: arguments(c)["status"].update(enum=["sale"])),
        ("an argument retyped", lambda c: arguments(c)["page"].update(type="string")),
        ("the path changed", lambda c: c["binding"]["http"].update(path="/items")),
        ("the method changed", lambda c: c["binding"]["http"].update(method="POST")),
        (
            "a response field removed",
            lambda c: c["interface"]["response"]["schema"]["properties"].pop("price"),
        ),
        (
            "a scope widened",
            lambda c: c["binding"]["http"]["auth"]["scopes"].append("orders.read"),
        ),
        (
            "execution became propose-apply",
            lambda c: c["governance"]["execution"].update(
                mode="propose-apply", humanApproval="required"
            ),
        ),
    ],
)
def test_taking_something_away_is_major(label, change):
    level, reasons = required_level(BASE, edit(change))
    assert level == "major", label
    assert reasons, label

    # A patch or a minor must not be enough to carry it.
    assert problems_for("t", BASE, edit(change, "1.1.2")), label
    assert problems_for("t", BASE, edit(change, "1.2.0")), label
    assert problems_for("t", BASE, edit(change, "2.0.0")) == [], label


@pytest.mark.parametrize(
    ("label", "change"),
    [
        (
            "an optional argument added",
            lambda c: arguments(c).update(brandId={"type": "string", "description": "x"}),
        ),
        (
            "a response field added",
            lambda c: c["interface"]["response"]["schema"]["properties"].update(
                url={"type": "string"}
            ),
        ),
    ],
)
def test_adding_something_is_minor(label, change):
    level, _ = required_level(BASE, edit(change))
    assert level == "minor", label

    assert problems_for("t", BASE, edit(change, "1.1.2")), label
    assert problems_for("t", BASE, edit(change, "1.2.0")) == [], label
    assert problems_for("t", BASE, edit(change, "2.0.0")) == [], label


@pytest.mark.parametrize(
    ("label", "change"),
    [
        ("a reworded description", lambda c: c["interface"].update(description="x" * 40)),
        (
            "a rendering change",
            lambda c: c["interface"]["response"]["ui"]["components"][1].update(variant="h3"),
        ),
        (
            "a longer cache ttl",
            lambda c: c["governance"]["caching"].update(ttlSeconds=600),
        ),
        (
            "a clearer error meaning",
            lambda c: c["binding"]["http"]["response"]["errors"]["statuses"][0].update(
                meaning="The store's token is missing or lacks the products.read scope entirely."
            ),
        ),
    ],
)
def test_wording_and_rendering_are_patch(label, change):
    level, _ = required_level(BASE, edit(change))
    assert level == "patch", label
    assert problems_for("t", BASE, edit(change, "1.1.2")) == [], label


def test_a_change_with_no_bump_at_all_is_rejected():
    """The failure this whole check exists for."""
    changed = edit(lambda c: arguments(c).pop("keyword"))  # version untouched
    (problem,) = problems_for("list_products", BASE, changed)

    assert "still '1.1.1'" in problem
    assert "needs a major bump" in problem
    # The reason has to name what went, or the author cannot act on it.
    assert "'keyword' was removed" in problem


def test_a_version_going_backwards_is_rejected():
    (problem,) = problems_for("t", BASE, edit(lambda c: arguments(c).pop("keyword"), "1.0.0"))
    assert "went backwards" in problem


def test_bump_level_reads_the_numbers():
    def at(version):
        return {"contractVersion": version}

    assert bump_level(at("1.1.1"), at("1.1.1")) == "none"
    assert bump_level(at("1.1.1"), at("1.1.2")) == "patch"
    assert bump_level(at("1.1.1"), at("1.2.0")) == "minor"
    assert bump_level(at("1.1.1"), at("2.0.0")) == "major"


def test_every_approved_contract_agrees_with_itself():
    """The lane compared against itself must be silent, or the gate cries wolf."""
    for path in sorted((REPO_ROOT / "contracts").glob("*.json")):
        contract = json.loads(path.read_text(encoding="utf-8"))
        assert problems_for(path.stem, contract, copy.deepcopy(contract)) == [], path.name
