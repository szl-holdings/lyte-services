"""Fail-closed decision-cell compiler and cited-jobs catalog (a11oy_factory)."""

from __future__ import annotations

import json
import re

import pytest

from a11oy_factory import ADMITTED, BLOCKED, CELLS, FRONTIERS, JOBS, compile_cell, search_jobs
from a11oy_factory.__main__ import main
from a11oy_factory.compiler import GENESIS, verify_receipt


def test_lyte_is_the_only_admitted_cell() -> None:
    assert ADMITTED == frozenset({"lyte"})
    assert [cell.id for cell in FRONTIERS] == [f"N{n}" for n in range(1, 9)]
    assert all(not cell.admitted and cell.honesty == "ROADMAP" for cell in FRONTIERS)


@pytest.mark.parametrize("cell_id", ["lyte", "LYTE", "  lyte  "])
def test_lyte_compiles_to_allow_with_a_verifiable_receipt(cell_id: str) -> None:
    receipt = compile_cell(cell_id, signal="checkout latency rose")
    assert receipt.decision == "ALLOW"
    assert receipt.cell == "lyte"
    assert receipt.honesty_tier == "STRUCTURAL-ONLY"
    assert receipt.bind == "BIND_AS_A11OY_PACKAGE"
    assert receipt.signer == "UNSIGNED-honest" and receipt.energy is None
    assert receipt.lambda_status == "Conjecture 1"
    assert verify_receipt(receipt.as_dict())


@pytest.mark.parametrize("cell_id", [*(f"N{n}" for n in range(1, 9)), "n3"])
def test_roadmap_cells_are_blocked(cell_id: str) -> None:
    receipt = compile_cell(cell_id)
    assert receipt.decision == BLOCKED
    assert receipt.cell == cell_id.upper()
    assert receipt.honesty_tier == "ROADMAP"
    assert receipt.note == CELLS[cell_id.upper()].note
    assert verify_receipt(receipt.as_dict())


@pytest.mark.parametrize(
    ("cell_id", "expected"), [("N9", "N9"), ("", "<empty>"), ("  ", "<empty>")]
)
def test_unknown_cells_fail_closed(cell_id: str, expected: str) -> None:
    receipt = compile_cell(cell_id)
    assert receipt.decision == BLOCKED
    assert receipt.honesty_tier == "UNAVAILABLE"
    assert receipt.bind == "UNBOUND"
    assert receipt.cell == expected
    assert verify_receipt(receipt.as_dict())


def test_receipt_hash_covers_every_emitted_field_and_detects_tampering() -> None:
    receipt = compile_cell("N1", signal="serve").as_dict()
    assert re.fullmatch(r"[0-9a-f]{64}", receipt["hash"])
    assert verify_receipt(receipt)
    forgeries = (("decision", "ALLOW"), ("honesty_tier", "LIVE"), ("signal_digest", "0" * 64))
    for field, forged in forgeries:
        assert not verify_receipt({**receipt, field: forged}), field
    without_digest = {key: value for key, value in receipt.items() if key != "signal_digest"}
    assert not verify_receipt(without_digest)
    assert not verify_receipt({**receipt, "extra": "field"})


def test_signal_is_bound_by_digest_and_never_stored() -> None:
    first = compile_cell("lyte", signal="secret-ish operator note")
    assert "secret-ish operator note" not in json.dumps(first.as_dict())
    assert compile_cell("lyte", signal="other").signal_digest != first.signal_digest
    other_cell = compile_cell("N1", signal="secret-ish operator note")
    assert other_cell.signal_digest != first.signal_digest


def test_prev_hash_links_receipts_and_malformed_links_restart_at_genesis() -> None:
    first = compile_cell("lyte")
    assert first.prev_hash == GENESIS
    second = compile_cell("lyte", prev_hash=first.hash)
    assert second.prev_hash == first.hash and verify_receipt(second.as_dict())
    assert compile_cell("lyte", prev_hash="abc").prev_hash == GENESIS


def test_search_jobs_is_a_local_catalog() -> None:
    everything = search_jobs("")
    assert len(everything["jobs"]) == len(JOBS)
    assert len(everything["cells"]) == 1 + len(FRONTIERS)
    vllm = search_jobs("vllm")
    assert [job["id"] for job in vllm["jobs"]] == ["vllm"]
    assert [cell["id"] for cell in vllm["cells"]] == ["N1"]
    assert search_jobs("no-such-leader")["jobs"] == []
    assert everything["signer"] == "UNSIGNED-honest" and everything["energy"] is None


def test_catalog_links_do_not_point_at_archived_repositories() -> None:
    # szl-holdings/immune-lattice is archived; its successor is immune/python.
    urls = {job.id: job.url for job in JOBS}
    assert urls["lattice"] == "https://github.com/szl-holdings/immune/tree/main/python"
    assert not any("szl-holdings/immune-lattice" in url for url in urls.values())


def test_cli_exit_codes_follow_the_decision(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["a11oy-factory", "compile", "--cell", "lyte"])
    assert main() == 0
    assert verify_receipt(json.loads(capsys.readouterr().out))
    monkeypatch.setattr("sys.argv", ["a11oy-factory", "compile", "--cell", "N2"])
    assert main() == 2
    assert json.loads(capsys.readouterr().out)["decision"] == BLOCKED
    monkeypatch.setattr("sys.argv", ["a11oy-factory", "search", "--q", "guidewire"])
    assert main() == 0
    assert [job["id"] for job in json.loads(capsys.readouterr().out)["jobs"]] == ["guidewire"]
