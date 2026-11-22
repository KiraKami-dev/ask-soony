import json

from ask_soony.dispatch import refresh, routes_for
from ask_soony.verifier.linker.tests.conftest import make_portal, write


def test_every_change_refreshes_verification():
    assert routes_for(["soony/erp/stock_snapshot.csv"]).verifier
    assert routes_for(["suppliers/SUP-1/contracts/terms.pdf"]).verifier


def test_only_event_and_logistics_changes_refresh_prediction():
    assert routes_for(["suppliers/SUP-1/notices/delay.pdf"]).predictor
    assert routes_for(["soony/logistics/open_shipments.csv"]).predictor
    assert not routes_for(["soony/master/bom.csv"]).predictor


class PredictorRecorder:
    def __init__(self):
        self.paths = []
        self.runs = []

    def events_for_paths(self, paths):
        self.paths.append(paths)
        return [{"changed": path} for path in paths if "news" in path]

    def run(self, events, force):
        self.runs.append((events, force))
        return events


def test_dispatches_erp_and_news_changes_to_their_roles(tmp_path):
    root = make_portal(tmp_path / "portal")
    write(root, "soony/news/port_strike.txt", "Port strike may delay containers.")
    out = tmp_path / "state" / "dispatch.json"
    predictor = PredictorRecorder()

    routed = refresh(
        ["soony/erp/stock_snapshot.csv", "soony/news/port_strike.txt"],
        root,
        tmp_path / "readings",
        out,
        predictor,
    )

    assert routed.verifier and routed.predictor
    assert predictor.paths == [["soony/erp/stock_snapshot.csv", "soony/news/port_strike.txt"]]
    assert predictor.runs == [([{"changed": "soony/news/port_strike.txt"}], True)]
    payload = json.loads(out.read_text())
    assert payload["changed_paths"] == [
        "soony/erp/stock_snapshot.csv",
        "soony/news/port_strike.txt",
    ]
    assert payload["routes"] == {"verifier": True, "predictor": True}
    assert payload["predictor"] == [{"changed": "soony/news/port_strike.txt"}]
    assert "cards" not in payload
    assert "decisions" in payload


def test_failing_predictor_does_not_stop_the_verifier(tmp_path):
    class Broken(PredictorRecorder):
        def run(self, events, force):
            raise TypeError("model down")

    root = make_portal(tmp_path / "portal")
    write(root, "soony/news/port_strike.txt", "Port strike.")
    out = tmp_path / "state" / "dispatch.json"
    routed = refresh(["soony/news/port_strike.txt"], root, tmp_path / "r", out, Broken())
    assert routed.predictor and out.is_file()
