from ask_soony.impact import _is_context_path, _json_items, _unknown_ids, is_event_path


def test_event_and_context_paths():
    assert is_event_path("soony/news/feed.json")
    assert is_event_path("suppliers/SUP-1/notices/x.pdf")
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
