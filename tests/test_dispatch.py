from ask_soony.dispatch import routes_for


def test_every_change_refreshes_verification():
    assert routes_for(["soony/erp/stock_snapshot.csv"]).verifier
    assert routes_for(["suppliers/SUP-1/contracts/terms.pdf"]).verifier


def test_only_event_and_logistics_changes_refresh_prediction():
    assert routes_for(["suppliers/SUP-1/notices/delay.pdf"]).predictor
    assert routes_for(["soony/logistics/open_shipments.csv"]).predictor
    assert not routes_for(["soony/master/bom.csv"]).predictor
