from ask_soony.api import option_cost, routine_type


def test_cost_is_read_from_text_and_blocks_routines():
    assert option_cost({"cost": "about EUR 16,800"}) == 16800
    assert option_cost({"cost": "€ 2.500"}) > 0
    assert option_cost({"cost": "No cost"}) == 0
    assert option_cost({"cost_value": 120}) == 120
    paid = {"cost": "about EUR 16,800", "routine": True, "type": "chase"}
    assert routine_type({}, paid) is None
    assert routine_type({}, {"cost": "No cost", "routine": True, "type": "chase"}) == "chase"


def test_covers_reads_how_much_of_the_gap_an_option_closes():
    from ask_soony.api import _covers

    assert _covers({"effect": "covers 5,900 of the 5,900 gap"}) == 5900
    assert _covers({"effect": "SAP shows the real stock again"}) == 0
