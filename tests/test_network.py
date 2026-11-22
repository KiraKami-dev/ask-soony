import json

import pytest
from fastapi.testclient import TestClient

from ask_soony.api import app


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def portal(tmp_path, monkeypatch):
    root = tmp_path / "portal"
    # Odd folder names on purpose: files are found by their columns.
    _write(root / "m" / "x.csv", "site_id,name,lat,lon\nS1,Fake Hub,50.0,8.0\n")
    _write(
        root / "m" / "p.csv",
        "locode,name,lat,lon,mode\nAAA,Alpha Port,10.0,100.0,sea\nBBB,Beta Port,52.0,9.0,sea\n"
        "ZZZ,Unused Port,0.0,0.0,sea\n",
    )
    _write(
        root / "m" / "r.csv",
        "route_id,origin,destination,mode,transit_days,via\n"
        "R1,AAA,BBB,sea,30,NOPE\nR2,BBB,S1,road,1,\n",
    )
    _write(
        root / "sup" / "SUP-9_Fake" / "profile.csv",
        "supplier_id,name,country,lat,lon\nSUP-9,Fake Supplier,XX,11.0,101.0\n",
    )
    _write(
        root / "sup" / "SUP-9_Fake" / "components.csv",
        "component_id,description,share,primary\nC-1,Thing,0.5,Y\n",
    )
    _write(
        root / "m" / "comp.csv",
        "component_id,description,category\nC-1,Thing,Gadgets\nC-2,Other,Boxes\n",
    )
    _write(root / "m" / "prod.csv", "product_id,name,day_one_qty\nFP-1,Fake Product,100\n")
    _write(
        root / "m" / "bom.csv",
        "parent_id,child_id,qty_per,valid_from,valid_to\nFP-1,C-1,2,2000-01-01,9999-12-31\n"
        "FP-1,C-2,1,2000-01-01,2001-01-01\n",
    )
    _write(
        root / "e" / "ship.json",
        json.dumps(
            {
                "shipments": [
                    {
                        "shipment_id": "SH-1",
                        "supplier_id": "SUP-9",
                        "route_id": "R1",
                        "dest_site": "S1",
                        "etd": "2030-01-01",
                        "eta": "2030-02-01",
                        "status": "in_transit",
                        "material_ids": "C-1",
                    },
                    {
                        "shipment_id": "SH-2",
                        "route_id": "missing",
                        "origin": "nowhere",
                        "destination": "also-nowhere",
                        "eta": "2030-02-01",
                    },
                ]
            }
        ),
    )
    monkeypatch.setenv("ASK_PORTAL_DIR", str(root))
    return TestClient(app)


def test_network_from_portal_dir(portal):
    net = portal.get("/api/network").json()
    assert net["source"] == "portal"
    ids = {n["id"]: n["kind"] for n in net["nodes"]}
    assert ids == {"S1": "site", "SUP-9": "supplier", "AAA": "port", "BBB": "port"}  # no ZZZ
    route = next(r for r in net["routes"] if r["id"] == "R1")
    assert route["path"] == ["AAA", "BBB"]  # unresolved via is dropped
    assert any(r["mode"] == "feeder" and r["origin"] == "SUP-9" for r in net["routes"])
    ship = net["shipments"][0]
    assert len(net["shipments"]) == 1  # the unresolvable one is skipped
    assert ship["path"] == ["AAA", "BBB", "S1"]  # chained on to the destination site
    assert net["part_suppliers"]["C-1"] == [{"supplier": "SUP-9", "share": 0.5}]
    prod = net["products"][0]
    assert [p["part"] for p in prod["parts"]] == ["C-1"]  # expired BOM line dropped
    assert prod["parts"][0]["description"] == "Thing" and prod["day_one"] == 100
    assert net["stale_hours"] == 24


def test_network_falls_back_to_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("ASK_PORTAL_DIR", str(tmp_path / "absent"))
    monkeypatch.setenv("ASK_STALE_HOURS", "6")
    net = TestClient(app).get("/api/network").json()
    assert net["source"] == "fixture" and net["nodes"] and net["stale_hours"] == 6


def test_network_empty_dir_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("ASK_PORTAL_DIR", str(tmp_path))
    assert TestClient(app).get("/api/network").json()["source"] == "fixture"
