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


def test_dispatches_erp_and_news_changes_to_their_roles(tmp_path):
    root = make_portal(tmp_path / "portal")
    write(root, "soony/news/port_strike.txt", "Port strike may delay containers.")
    out = tmp_path / "state" / "decision_cards.html"
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
    assert out.exists()
    assert json.loads(out.with_suffix(".json").read_text())
