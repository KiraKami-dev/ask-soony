from ask_soony.predictor import (
    _is_context_path,
    _json_items,
    _unknown_ids,
    is_event_path,
    is_predictor_path,
)


def test_event_and_context_paths():
    assert is_event_path("soony/news/feed.json")
    assert is_event_path("suppliers/SUP-1/notices/x.pdf")
    assert is_predictor_path("soony/logistics/open_shipments.csv")
    assert not is_event_path("soony/master/bom.csv")
    assert _is_context_path("soony/master/bom.csv")
    assert _is_context_path("suppliers/SUP-1/components.csv")
    assert not _is_context_path("suppliers/SUP-1/notices/price.xlsx")
    assert not _is_context_path("soony/receiving/DC/scan.jpg")


def test_news_feed_split_into_items():
    assert _json_items('{"items": [{"id": "N1"}, {"id": "N2"}]}') == [{"id": "N1"}, {"id": "N2"}]
    assert _json_items("not json") == []


def test_unknown_ids_flagged():
    r = {"affected": [{"supplier_id": "SUP-1", "component_id": "CMP-X", "product_ids": ["FG-1"]}]}
    assert _unknown_ids(r, "SUP-1 FG-1") == ["CMP-X"]


class _Store:
    """A watcher store that has seen nothing yet (a file dropped into the mirror by hand)."""

    def current(self):
        return {}


class _Backend:
    model = "fake"

    def run(self, system, text, images, tools, handler, should_stop, max_turns=12):
        from ask_soony.llm import RunResult

        handler(
            "submit_impact",
            {
                "event_summary": "Port closed.",
                "event_type": "weather",
                "overall_launch_risk": "yes",
                "recommendation": "Ask the supplier.",
                "affected": [
                    {
                        "supplier_id": "SUP-9",
                        "component_id": "CMP-1",
                        "product_ids": ["P-A"],
                        "effect": "delay",
                        "mechanism": "port",
                        "launch_risk": "yes",
                        "severity": "high",
                        "evidence": "x",
                    }
                ],
            },
        )
        return RunResult("", 1, "tool_use")


def test_event_impacts_do_not_collide_with_the_paper_check(tmp_path, monkeypatch):
    """The paper check keeps impacts.json as a list; the predictor writes event_impacts.json,
    reads a dropped file from the mirror, and its result becomes a Today card."""
    from ask_soony import api
    from ask_soony.predictor import ImpactAgent

    portal, state = tmp_path / "portal", tmp_path / "state"
    (portal / "soony" / "news").mkdir(parents=True)
    (portal / "soony" / "news" / "n.json").write_text('{"items": [{"id": "N1", "t": "x"}]}')
    state.mkdir()
    (state / "impacts.json").write_text('[{"finding_id": "paper-1", "units_short": 5}]')
    monkeypatch.setenv("ASK_STATE_DIR", str(state))
    agent = ImpactAgent(_Store(), _Backend(), state, portal)
    agent.network_context = lambda: "SUP-9 CMP-1 P-A"
    done = agent.run(agent.events_for_paths(["soony/news/n.json"]), force=True)
    assert [d["event_id"] for d in done] == ["N1"]
    assert api.read_list("impacts.json") == [{"finding_id": "paper-1", "units_short": 5}]
    assert list(api.read_json("event_impacts.json", {})) == ["N1"]
    cards = api.event_cards({"nodes": {}}, {}, {})
    assert [(c["kind"], c["level"], c["headline"]) for c in cards] == [
        ("risk_news", "amber", "Port closed.")
    ]


def test_older_impacts_keyed_by_event_still_read(tmp_path, monkeypatch):
    from ask_soony import api

    monkeypatch.setenv("ASK_STATE_DIR", str(tmp_path))
    (tmp_path / "impacts.json").write_text(
        '{"E1": {"overall_launch_risk": "no", "event_summary": "Old", "affected": []}}'
    )
    assert [c["headline"] for c in api.event_cards({"nodes": {}}, {}, {})] == ["Old"]
