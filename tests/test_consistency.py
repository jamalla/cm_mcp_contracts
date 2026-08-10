"""Cross-references JSON Schema cannot express.

A schema can say `parameters.path` is an array of objects. It cannot say the array
must line up with the {placeholders} in `path`, or that every `from` must name an
argument the tool accepts. Those mismatches validate perfectly and then build a
broken request -- a URL with a literal {coupon_id} in it, or a filter silently
dropped because it reads an argument nobody can pass.

These checks found a real bug in this repo's own test fixtures the first time they
ran, which is roughly the point.
"""

import copy
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_contracts import consistency_problems  # noqa: E402
from tests.test_salla_rules import DELETE_CONTRACT, READ_CONTRACT  # noqa: E402


def test_valid_contracts_are_consistent():
    assert consistency_problems(READ_CONTRACT) == []
    assert consistency_problems(DELETE_CONTRACT) == []


def test_unmapped_path_placeholder_is_caught():
    """Otherwise the engine requests /coupons/{coupon_id} literally."""
    contract = copy.deepcopy(DELETE_CONTRACT)
    contract["binding"]["http"]["parameters"] = {}
    problems = consistency_problems(contract)
    assert any("{coupon_id}" in p and "no entry" in p for p in problems), problems


def test_mapping_a_placeholder_the_path_lacks_is_caught():
    contract = copy.deepcopy(DELETE_CONTRACT)
    contract["binding"]["http"]["parameters"]["path"].append(
        {"name": "store_id", "from": "couponId"}
    )
    problems = consistency_problems(contract)
    assert any("store_id" in p and "does not appear in path" in p for p in problems), problems


def test_mapping_reading_an_undeclared_argument_is_caught():
    """The bug this whole module exists for: a filter wired to nothing."""
    contract = copy.deepcopy(READ_CONTRACT)
    contract["binding"]["http"]["parameters"]["query"] = [
        {"name": "status", "from": "couponStatus"}
    ]
    problems = consistency_problems(contract)
    assert any("couponStatus" in p for p in problems), problems


def test_a_required_argument_that_is_never_sent_is_caught():
    """Requiring an argument and then dropping it is a promise the tool cannot keep."""
    contract = copy.deepcopy(READ_CONTRACT)
    contract["interface"]["input"]["schema"]["properties"]["status"] = {"type": "string"}
    contract["interface"]["input"]["schema"]["required"] = ["status"]
    problems = consistency_problems(contract)
    assert any("'status'" in p and "never used" in p for p in problems), problems


def test_passthrough_body_consumes_the_remaining_arguments():
    """A passthrough body sends whatever the path and query did not, so nothing is orphaned."""
    contract = copy.deepcopy(DELETE_CONTRACT)
    contract["binding"]["http"]["method"] = "POST"
    contract["binding"]["http"]["path"] = "/coupons"
    contract["binding"]["http"]["parameters"] = {"body": {"mode": "passthrough"}}
    contract["interface"]["input"]["schema"]["properties"]["code"] = {"type": "string"}
    contract["interface"]["input"]["schema"]["required"] = ["couponId", "code"]
    assert consistency_problems(contract) == []


def test_mapped_body_field_reading_an_undeclared_argument_is_caught():
    contract = copy.deepcopy(DELETE_CONTRACT)
    contract["binding"]["http"]["method"] = "POST"
    contract["binding"]["http"]["path"] = "/coupons"
    contract["binding"]["http"]["parameters"] = {
        "body": {"mode": "mapped", "fields": [{"name": "code", "from": "couponCode"}]}
    }
    problems = consistency_problems(contract)
    assert any("couponCode" in p for p in problems), problems


def test_cache_key_naming_a_missing_argument_is_caught():
    contract = copy.deepcopy(READ_CONTRACT)
    contract["governance"]["caching"]["keyBy"] = ["couponId"]
    problems = consistency_problems(contract)
    assert any("keyBy" in p and "couponId" in p for p in problems), problems


def test_validation_rule_guarding_a_missing_argument_is_caught():
    """A guard on a field nobody can pass never runs, so it is worse than absent."""
    contract = copy.deepcopy(READ_CONTRACT)
    contract["validation"] = {
        "rules": [{"field": "couponId", "match": "^[0-9]+$", "message": "must be numeric"}]
    }
    problems = consistency_problems(contract)
    assert any("validation rule" in p and "couponId" in p for p in problems), problems


def test_a_constant_query_parameter_needs_no_argument():
    """A pinned filter is deliberately not agent-controlled, so it orphans nothing."""
    contract = copy.deepcopy(READ_CONTRACT)
    contract["binding"]["http"]["parameters"]["query"].append(
        {"name": "per_page", "constant": 50}
    )
    assert consistency_problems(contract) == []


def test_builtin_bindings_are_skipped():
    """A builtin has no path, query or scopes to cross-check."""
    contract = copy.deepcopy(READ_CONTRACT)
    contract["binding"] = {"type": "none", "handler": "builtin://summarize_coupons"}
    assert consistency_problems(contract) == []
