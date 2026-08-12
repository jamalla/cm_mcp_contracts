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

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_contracts import (  # noqa: E402
    approved_contracts,
    consistency_problems,
    dependency_problems,
    resolver_problems,
    tool_names,
)
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


# -- dependency edges ----------------------------------------------------------
#
# The check above compares a contract against itself. This one compares contracts
# against each other, which is the only way to see that a declared dependency
# points at a tool nobody ever wrote -- the state `list_orders` shipped in.


def test_a_dependency_naming_a_real_contract_resolves():
    contract = copy.deepcopy(READ_CONTRACT)
    contract["dependencies"] = [{"contract": "delete_coupon", "reason": "ids come from there"}]
    assert dependency_problems(contract, {"list_fixture_coupons", "delete_coupon"}) == []


def test_a_dependency_naming_a_missing_contract_is_caught():
    """The bug this check exists for: an edge the agent is told to follow, to nowhere."""
    contract = copy.deepcopy(READ_CONTRACT)
    contract["dependencies"] = [
        {"contract": "list_coupon_statuses", "reason": "the status filter takes real slugs"}
    ]
    problems = dependency_problems(contract, {"list_fixture_coupons"})
    assert any("list_coupon_statuses" in p and "not a contract" in p for p in problems), problems


def test_a_contract_cannot_depend_on_itself():
    contract = copy.deepcopy(READ_CONTRACT)
    contract["dependencies"] = [
        {"contract": "list_fixture_coupons", "reason": "circular by mistake"}
    ]
    problems = dependency_problems(contract, {"list_fixture_coupons"})
    assert any("itself" in p for p in problems), problems


def test_no_dependencies_is_not_a_problem():
    """Most contracts stand alone; absence of the key must stay silent."""
    assert dependency_problems(READ_CONTRACT, set()) == []
    assert dependency_problems({**READ_CONTRACT, "dependencies": []}, set()) == []


def test_every_approved_contract_resolves_its_dependencies():
    """The lane itself, held to the rule -- not just a synthetic contract."""
    lane = approved_contracts()
    known = tool_names(lane)
    for contract in lane:
        assert dependency_problems(contract, known) == [], contract["interface"]["name"]


# -- value resolvers -------------------------------------------------------
#
# A resolver copies the lookup's endpoint into the calling contract, because the
# generated module runs in a sandbox with no registry to consult. The copy is
# only safe while something compares it to the original -- that is what these
# are. The rest are about blast radius: a resolver is an extra call made on the
# caller's credential, before the call anyone asked for.

LOOKUP = {
    "interface": {
        "name": "list_order_statuses",
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    "binding": {
        "type": "http",
        "http": {
            "method": "GET",
            "path": "/orders/statuses",
            "auth": {"scopes": ["orders.read"]},
            "response": {"dataPath": "data"},
        },
    },
}


def _with_resolver(**overrides):
    contract = copy.deepcopy(READ_CONTRACT)
    contract["binding"]["http"]["auth"]["scopes"] = ["orders.read"]
    contract["binding"]["http"]["parameters"]["query"] = [
        {
            "name": "status",
            "resolve": {
                "contract": "list_order_statuses",
                "path": "/orders/statuses",
                "dataPath": "data",
                "matchOn": ["slug"],
                "sendField": "id",
                **overrides,
            },
        }
    ]
    contract["dependencies"] = [
        {"contract": "list_order_statuses", "reason": "the status filter resolves through it"}
    ]
    return contract


LANE = {"list_order_statuses": LOOKUP}


def test_a_well_formed_resolver_passes():
    assert resolver_problems(_with_resolver(), LANE) == []


def test_a_resolver_must_also_be_declared_as_a_dependency():
    """Otherwise the edge is invisible to every other check, including the gate's."""
    contract = _with_resolver()
    contract["dependencies"] = []
    problems = resolver_problems(contract, LANE)
    assert any("dependencies" in p for p in problems), problems


def test_a_resolver_path_that_drifted_from_its_contract_is_caught():
    """The whole reason the copy is allowed is that this runs."""
    problems = resolver_problems(_with_resolver(path="/orders/states"), LANE)
    assert any("drifted" in p for p in problems), problems


def test_a_resolver_data_path_that_disagrees_is_caught():
    problems = resolver_problems(_with_resolver(dataPath="items"), LANE)
    assert any("unwraps" in p for p in problems), problems


def test_a_resolver_may_not_name_a_tool_that_writes():
    """It runs before the call the caller asked for. It cannot have side effects."""
    lane = copy.deepcopy(LANE)
    lane["list_order_statuses"]["interface"]["annotations"]["readOnlyHint"] = False
    problems = resolver_problems(_with_resolver(), lane)
    assert any("read-only" in p for p in problems), problems


def test_a_resolver_may_not_widen_the_scopes_the_tool_asked_for():
    """Least privilege: the extra call rides on the same credential."""
    lane = copy.deepcopy(LANE)
    lane["list_order_statuses"]["binding"]["http"]["auth"]["scopes"] = ["customers.read"]
    problems = resolver_problems(_with_resolver(), lane)
    assert any("customers.read" in p for p in problems), problems


def test_a_contract_with_no_resolver_is_silent():
    assert resolver_problems(READ_CONTRACT, LANE) == []


def test_the_approved_lane_satisfies_its_own_resolvers():
    lane = {c["interface"]["name"]: c for c in approved_contracts()}
    for contract in lane.values():
        assert resolver_problems(contract, lane) == [], contract["interface"]["name"]
