"""The rules the meta-schema exists to enforce, modeled on the Salla API's nature.

Anyone can write a schema that checks required fields. The rules worth having are
the ones that catch a contract which is well-formed and still wrong: a write
marked read-only (so the engine would cache it and serve a stale result as though
the write had happened), a read asking for a read_write scope (so every merchant
who installs the app grants more access than the job needs), a DELETE nobody has
to approve.

Each test below starts from a valid contract and breaks exactly one thing, so a
failure names the rule that stopped working.
"""

import copy
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SCHEMA_PATH = REPO_ROOT / "schema" / "tool-contract.v2.json"

# A minimal, valid Salla read contract. Every test mutates a copy of this.
READ_CONTRACT = {
    "$schema": "../schema/tool-contract.v2.json",
    "contractVersion": "1.0.0",
    "kind": "single-tool",
    "interface": {
        "name": "list_fixture_coupons",
        "title": "List Coupons",
        "description": "Lists the discount coupons configured in the store.",
        "whenToUse": ["The merchant asks which coupons exist or are active."],
        "whenNotToUse": ["The merchant named one coupon -- use get_coupon instead."],
        "input": {
            "schema": {
                "type": "object",
                "properties": {"page": {"type": "integer"}},
                "additionalProperties": False,
            }
        },
        "response": {"schema": {"type": "object", "properties": {"id": {"type": "integer"}}}},
    },
    "binding": {
        "type": "http",
        "http": {
            "method": "GET",
            "path": "/coupons",
            "auth": {"scopes": ["coupons.read"]},
            "parameters": {"query": [{"name": "page"}]},
            "response": {
                "dataPath": "data",
                "collection": True,
                "pagination": "standard",
                "successStatuses": [200],
            },
        },
    },
    "governance": {
        "execution": {"mode": "direct", "humanApproval": "never"},
        "caching": {"cacheable": True, "keyBy": ["page"], "ttlSeconds": 300},
    },
}
READ_CONTRACT["interface"]["annotations"] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

# A minimal, valid Salla delete contract -- the strictest combination the schema allows.
DELETE_CONTRACT = copy.deepcopy(READ_CONTRACT)
DELETE_CONTRACT["interface"]["name"] = "delete_coupon"
DELETE_CONTRACT["interface"]["description"] = "Deletes a discount coupon from the store."
DELETE_CONTRACT["interface"]["input"] = {
    "schema": {
        "type": "object",
        "properties": {"couponId": {"type": "integer", "description": "Salla coupon id."}},
        "required": ["couponId"],
        "additionalProperties": False,
    }
}
DELETE_CONTRACT["binding"]["http"].update(
    {
        "method": "DELETE",
        "path": "/coupons/{coupon_id}",
        "auth": {"scopes": ["coupons.read_write"]},
        "parameters": {"path": [{"name": "coupon_id", "from": "couponId"}]},
        "response": {"dataPath": "data", "collection": False, "successStatuses": [202]},
    }
)
DELETE_CONTRACT["interface"]["annotations"] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}
DELETE_CONTRACT["governance"] = {
    "execution": {"mode": "propose-apply", "humanApproval": "required"},
    "caching": {"cacheable": False},
}


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def broken(base: dict, **changes):
    """A copy of `base` with a nested path overwritten, e.g. governance__caching."""
    contract = copy.deepcopy(base)
    for dotted, value in changes.items():
        keys = dotted.split("__")
        target = contract
        for key in keys[:-1]:
            target = target[key]
        if value is None:
            target.pop(keys[-1], None)
        else:
            target[keys[-1]] = value
    return contract


def errors(validator, contract) -> list[str]:
    return [e.message for e in validator.iter_errors(contract)]


# -- the baselines must be valid, or every test below is meaningless -----------


def test_the_read_baseline_is_valid(validator):
    assert not errors(validator, READ_CONTRACT)


def test_the_delete_baseline_is_valid(validator):
    assert not errors(validator, DELETE_CONTRACT)


# -- method vs annotations -----------------------------------------------------


def test_get_must_be_read_only(validator):
    contract = copy.deepcopy(READ_CONTRACT)
    contract["interface"]["annotations"]["readOnlyHint"] = False
    contract["governance"]["caching"] = {"cacheable": False}
    contract["binding"]["http"]["auth"] = {"scopes": ["coupons.read_write"]}
    assert errors(validator, contract), "a GET declared not-read-only should be rejected"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_writes_cannot_claim_to_be_read_only(validator, method):
    contract = broken(READ_CONTRACT)
    contract["binding"]["http"]["method"] = method
    assert errors(validator, contract), f"{method} marked read-only should be rejected"


def test_delete_must_be_destructive(validator):
    contract = copy.deepcopy(DELETE_CONTRACT)
    contract["interface"]["annotations"]["destructiveHint"] = False
    assert errors(validator, contract)


def test_delete_must_require_human_approval(validator):
    contract = copy.deepcopy(DELETE_CONTRACT)
    contract["governance"]["execution"] = {"mode": "direct", "humanApproval": "never"}
    assert errors(validator, contract)


# -- least privilege on scopes -------------------------------------------------


def test_a_read_only_tool_cannot_request_a_write_scope(validator):
    """The rule that protects every merchant who installs the app."""
    contract = copy.deepcopy(READ_CONTRACT)
    contract["binding"]["http"]["auth"]["scopes"] = ["coupons.read_write"]
    assert errors(validator, contract)


def test_a_write_tool_cannot_run_on_a_read_scope(validator):
    """Salla would answer with a 401 at runtime; reject it at review time."""
    contract = copy.deepcopy(DELETE_CONTRACT)
    contract["binding"]["http"]["auth"]["scopes"] = ["coupons.read"]
    assert errors(validator, contract)


@pytest.mark.parametrize(
    "scope", ["coupons.write", "coupons", "Coupons.read", "coupons.read-write", "read"]
)
def test_malformed_scopes_are_rejected(validator, scope):
    contract = copy.deepcopy(READ_CONTRACT)
    contract["binding"]["http"]["auth"]["scopes"] = [scope]
    assert errors(validator, contract), f"{scope!r} is not a Salla scope"


def test_scopes_are_mandatory(validator):
    """Without a declared scope nobody can review what access the tool needs."""
    contract = copy.deepcopy(READ_CONTRACT)
    del contract["binding"]["http"]["auth"]
    assert errors(validator, contract)


# -- caching -------------------------------------------------------------------


def test_only_read_only_tools_may_be_cached(validator):
    contract = copy.deepcopy(DELETE_CONTRACT)
    contract["governance"]["caching"] = {"cacheable": True, "ttlSeconds": 60}
    assert errors(validator, contract)


def test_a_cacheable_tool_must_set_a_ttl(validator):
    """Otherwise the merchant's stale data is served forever."""
    contract = copy.deepcopy(READ_CONTRACT)
    contract["governance"]["caching"] = {"cacheable": True, "keyBy": ["page"]}
    assert errors(validator, contract)


# -- the Salla envelope --------------------------------------------------------


def test_a_paginated_response_must_be_a_collection(validator):
    contract = copy.deepcopy(READ_CONTRACT)
    contract["binding"]["http"]["response"]["collection"] = False
    assert errors(validator, contract)


def test_data_path_is_required(validator):
    """The engine needs to know what to unwrap out of Salla's envelope."""
    contract = copy.deepcopy(READ_CONTRACT)
    del contract["binding"]["http"]["response"]["dataPath"]
    assert errors(validator, contract)


# -- failure declarations --------------------------------------------------------


ERRORS_BLOCK = {
    "messagePath": "error.message",
    "fieldsPath": "error.fields",
    "statuses": [
        {"status": 401, "meaning": "the app's token lacks the categories.read scope"},
        {"status": 404, "meaning": "no category exists with this id"},
        {"status": 429, "meaning": "rate limited by the upstream", "retryable": True},
    ],
}


def test_documented_failures_are_accepted(validator):
    """An endpoint can declare its 4xx/5xx statuses with agent-facing meanings."""
    contract = copy.deepcopy(READ_CONTRACT)
    contract["binding"]["http"]["response"]["errors"] = ERRORS_BLOCK
    assert not errors(validator, contract)


def test_error_statuses_must_be_failures(validator):
    """2xx belongs in successStatuses; the ranges keep the two disjoint by construction."""
    contract = copy.deepcopy(READ_CONTRACT)
    contract["binding"]["http"]["response"]["errors"] = {
        "statuses": [{"status": 200, "meaning": "definitely not an error status"}]
    }
    assert errors(validator, contract)


def test_error_meanings_are_required_and_substantial(validator):
    """A bare status code tells the agent nothing it could not guess."""
    contract = copy.deepcopy(READ_CONTRACT)
    contract["binding"]["http"]["response"]["errors"] = {"statuses": [{"status": 404}]}
    assert errors(validator, contract)

    contract["binding"]["http"]["response"]["errors"] = {
        "statuses": [{"status": 404, "meaning": "404"}]
    }
    assert errors(validator, contract), "a meaning shorter than 10 chars should be rejected"


# -- parameter mapping ---------------------------------------------------------


def test_a_query_parameter_cannot_be_both_constant_and_argument_fed(validator):
    contract = copy.deepcopy(READ_CONTRACT)
    contract["binding"]["http"]["parameters"]["query"] = [
        {"name": "status", "from": "status", "constant": "active"}
    ]
    assert errors(validator, contract)


def test_mapped_body_requires_fields(validator):
    """mode 'mapped' with no fields sends an empty body, which is never intended."""
    contract = copy.deepcopy(DELETE_CONTRACT)
    contract["binding"]["http"]["parameters"]["body"] = {"mode": "mapped"}
    assert errors(validator, contract)


# -- the binding surface -------------------------------------------------------


def test_an_unknown_binding_type_is_not_accepted(validator):
    """Only http (a configured upstream) and none (a builtin) exist."""
    contract = copy.deepcopy(READ_CONTRACT)
    contract["binding"] = {"type": "grpc", "grpc": {"service": "coupons"}}
    assert errors(validator, contract)


def test_the_upstream_is_selected_by_name_not_by_url(validator):
    """binding.http.api names a configured upstream; the engine owns its host."""
    contract = copy.deepcopy(READ_CONTRACT)
    contract["binding"]["http"]["api"] = "salla"
    assert not errors(validator, contract)


def test_no_base_url_may_be_pinned_in_a_contract(validator):
    """The engine owns the host, so a contract cannot pin production or drift."""
    contract = copy.deepcopy(READ_CONTRACT)
    contract["binding"]["http"]["baseUrl"] = "https://api.salla.dev/admin/v2"
    assert errors(validator, contract)


def test_a_builtin_binding_is_still_allowed(validator):
    """Helpers that compute rather than fetch stay possible."""
    contract = copy.deepcopy(READ_CONTRACT)
    contract["binding"] = {"type": "none", "handler": "builtin://summarize_coupons"}
    assert not errors(validator, contract)
