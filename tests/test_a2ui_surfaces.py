"""A2UI surfaces that would render blank.

Every failure here shares one property: the client does not complain. A binding
that names a field the tool never returns, a row template pointed at the wrong
path, a child id with a typo -- each renders as empty space, in front of a
merchant, with nothing in any log to say why. There is no runtime signal to fall
back on, so the surface is checked harder than anything else in a contract.

The scope rule is the one that catches people. Inside a template a path is
RELATIVE to the item (`name`), and everywhere else it is absolute (`/name`).
Both spellings are valid JSON, both satisfy the schema, and exactly one of them
shows the merchant their products.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_contracts import approved_contracts, surface_problems  # noqa: E402

COLLECTION = json.loads((REPO_ROOT / "contracts" / "list_products.json").read_text(encoding="utf-8"))
DETAIL = json.loads((REPO_ROOT / "contracts" / "get_product.json").read_text(encoding="utf-8"))


def components(contract: dict) -> list[dict]:
    return contract["interface"]["response"]["ui"]["components"]


def component(contract: dict, cid: str) -> dict:
    return next(c for c in components(contract) if c["id"] == cid)


def mutate(base: dict, cid: str, **changes) -> dict:
    contract = copy.deepcopy(base)
    component(contract, cid).update(changes)
    return contract


def test_every_approved_surface_resolves():
    """The lane itself, which is the case that has to keep passing."""
    for contract in approved_contracts():
        name = contract["interface"]["name"]
        assert surface_problems(contract) == [], name


def test_a_contract_without_a_surface_is_allowed():
    """`ui` is optional -- raw JSON is a legitimate rendering for some tools."""
    contract = copy.deepcopy(DETAIL)
    del contract["interface"]["response"]["ui"]
    assert surface_problems(contract) == []


# Each case is a single realistic slip. The message must name what to fix.
@pytest.mark.parametrize(
    ("label", "contract", "expected"),
    [
        (
            "a field the tool does not return",
            mutate(COLLECTION, "row_name", text={"path": "nmae"}),
            "nmae",
        ),
        (
            "absolute where it should be relative",
            mutate(COLLECTION, "row_name", text={"path": "/name"}),
            "relative to the item",
        ),
        (
            "relative with no template around it",
            mutate(COLLECTION, "heading", text={"path": "count"}),
            "no item to be relative to",
        ),
        (
            "a child id that does not exist",
            mutate(COLLECTION, "row", children=["row_name", "ghost"]),
            "ghost",
        ),
        (
            "a template over something not returned",
            mutate(COLLECTION, "rows", children={"componentId": "row", "path": "/nope"}),
            "not part of this tool's result",
        ),
        (
            "a template over something that is not a list",
            mutate(COLLECTION, "rows", children={"componentId": "row", "path": "/count"}),
            "not a list",
        ),
        (
            "a template component that does not exist",
            mutate(COLLECTION, "rows", children={"componentId": "missing", "path": "/items"}),
            "missing",
        ),
        (
            "components nothing reaches",
            mutate(COLLECTION, "root", children=["heading"]),
            "would never render",
        ),
        (
            "a typo inside a ${} interpolation",
            mutate(
                COLLECTION,
                "row_stock",
                text={"call": "formatString", "args": {"value": "${quantiy} left"}},
            ),
            "quantiy",
        ),
        (
            "a card naming a child that does not exist",
            mutate(DETAIL, "root", child="nowhere"),
            "nowhere",
        ),
    ],
)
def test_broken_surfaces_are_caught(label, contract, expected):
    problems = surface_problems(contract)
    assert problems, f"{label}: nothing was reported"
    assert any(expected in problem for problem in problems), f"{label}: {problems}"


def test_a_surface_needs_a_root():
    contract = mutate(COLLECTION, "root", id="top")
    assert any("root" in problem for problem in surface_problems(contract))


def test_duplicate_ids_are_caught():
    contract = copy.deepcopy(COLLECTION)
    components(contract).append({"id": "row_name", "component": "Text", "text": "duplicate"})
    assert any("two components" in problem for problem in surface_problems(contract))


def test_detail_surfaces_read_from_the_record_itself():
    """A detail tool has no envelope, so `/name` is the record's own field.

    The same pointer means different things either side of `collection`, which is
    why the check models both rather than assuming one shape.
    """
    assert surface_problems(DETAIL) == []
    assert any(
        "not a field" in problem
        for problem in surface_problems(mutate(DETAIL, "name", text={"path": "/items/name"}))
    )
