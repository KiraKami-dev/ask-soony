"""Supplier master changes: newer profiles, unconfirmed contact addresses, bank detail notices."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from ask_soony import anchors, api, supplier_master
from ask_soony.cardtools import supplier_addresses
from ask_soony.routines import supplier_email

NOW = datetime(2030, 6, 1, 21, 0, tzinfo=ZoneInfo("Europe/Berlin"))
OLD, NEW = "sales@example.com", "billing@example.net"


@pytest.fixture
def portal(tmp_path, monkeypatch):
    root = tmp_path / "portal"
    files = {
        "soony/master/products.csv": "product_id,name,lifecycle\nP-CE,Thing – Ce,launch\n",
        "soony/master/bom.csv": "parent_id,child_id,qty_per\nP-CE,PART-S,1\n",
        "soony/master/components.csv": "component_id,description\nPART-S,Statue (die-cast)\n",
        "suppliers/SUP-7_Cast/profile.csv": "supplier_id,name,status,qualification_status,"
        "contact_name,contact_email,phone,notes\n"
        f"SUP-7,Cast Works,active,conditional,Ann Lee,{OLD},+49 30 1111 2222,Second source\n",
        "suppliers/SUP-7_Cast/components.csv": "component_id,description,share\n"
        "PART-S,Statue (die-cast),0.2\n",
        "suppliers/SUP-7_Cast/profile.json": json.dumps(
            {
                "supplier": {
                    "supplierId": "SUP-7",
                    "status": "watch",
                    "qualification": {"status": "conditional", "validUntil": "2030-05-30"},
                    "contact": {"name": "Ann Lee", "email": NEW, "phone": "+49 30 1111 2222"},
                    "notes": "Review by Supplier Quality open.",
                }
            }
        ),
        "suppliers/SUP-7_Cast/contracts/agreement.txt": "Payment to IBAN DE89 3704 0044 0532 "
        "0130 00 within 30 days.\n",
        "suppliers/SUP-7_Cast/notices/bank_details_change.txt": "Notice of change of bank "
        "details. Please pay to our new account IBAN GB33 BUKB 2020 1555 5555 55. "
        "Questions: +44 20 7000 0000\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    monkeypatch.setenv("ASK_PORTAL_DIR", str(root))
    monkeypatch.setenv("ASK_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ASK_DATA_DIR", str(tmp_path / "store"))
    with anchors._lock:
        anchors._index.clear()
    return root


def _cards(root):
    return {c["id"].split("-")[1]: c for c in supplier_master.cards({"root": root}, NOW, {}, "")}


def test_newer_profile_status_becomes_a_launch_card(portal):
    c = _cards(portal)["status"]
    assert c["level"] == "amber" and c["status"] == "open"
    assert "Cast Works is now 'watch'" in c["headline"]
    assert "expired 30 May" in c["headline"] and "Statue (20%)" in c["headline"]
    assert any("profile.json" in e["source"] for e in c["evidence"])


def test_new_contact_address_is_flagged_not_trusted(portal):
    c = _cards(portal)["contact"]
    assert NEW in c["summary"] and c["level"] == "amber"
    assert OLD in supplier_addresses() and NEW not in supplier_addresses()
    assert supplier_email("SUP-7") == OLD
    # A person confirms the address (the trust option): from then on it is a send target.
    api.write_json(
        "actions.json",
        [
            {
                "decision_id": c["id"],
                "kind": "approve",
                "option": supplier_master.TRUST_OPTION,
                "draft": c["options"][supplier_master.TRUST_OPTION]["draft"],
            }
        ],
    )
    assert NEW in supplier_addresses()
    assert "contact" not in _cards(portal)


def test_bank_details_change_is_a_red_card_never_auto(portal):
    c = _cards(portal)["bank"]
    assert c["level"] == "red" and c["kind"] == "bank_details" and c["trusted_types"] == []
    assert c["headline"].startswith("Possible invoice fraud: IBAN differs from the contract")
    assert "GB33BUKB20201555555555" in c["summary"] and "DE89370400440532013000" in c["summary"]
    call = c["options"][0]
    assert call["recommended"] and "+49 30 1111 2222" in call["action"]
    assert "+44" not in call["action"]  # never the number in the notice
    assert "Hold all payments" in c["options"][1]["action"]
    assert all(o["channel"] == "notify" for o in c["options"])  # recorded, nothing is sent


def test_notice_repeating_the_contract_iban_is_quiet(portal):
    (portal / "suppliers/SUP-7_Cast/notices/bank_details_change.txt").write_text(
        "Reminder of our bank details: IBAN DE89 3704 0044 0532 0130 00.\n"
    )
    assert "bank" not in _cards(portal)


def test_master_only_supplier_has_no_cards(portal):
    (portal / "suppliers/SUP-7_Cast/profile.json").unlink()
    (portal / "suppliers/SUP-7_Cast/notices/bank_details_change.txt").unlink()
    assert _cards(portal) == {}
