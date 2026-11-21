from datetime import datetime

from ask_soony.verifier.impact.timing import hours_left, lead_hours, runs_dry, shift_windows

SHIFTS = [{"day": "Saturday", "shift": "06:00–22:00"}, {"day": "Sunday", "shift": "06:00–20:00"}]


def test_shifts_are_placed_on_the_days_before_launch():
    windows = shift_windows(SHIFTS, "2026-11-22")  # a Sunday
    assert windows == [
        (datetime(2026, 11, 21, 6), datetime(2026, 11, 21, 22)),
        (datetime(2026, 11, 22, 6), datetime(2026, 11, 22, 20)),
    ]


def test_runs_dry_skips_the_night():
    windows = shift_windows(SHIFTS, "2026-11-22")
    now = datetime(2026, 11, 21, 20, 50)
    assert round(hours_left(windows, now), 2) == 15.17
    # 1h10 left on Saturday, so 6.44 h of stock runs out on Sunday at 11:16
    assert runs_dry(windows, now, 5800 / 900).strftime("%a %H:%M") == "Sun 11:16"
    assert runs_dry(windows, now, 100) is None  # lasts past the end of packing


def test_lead_hours_count_each_text_once():
    texts = [
        "załadunek w 4 h od zamówienia / loaded within 4 h of your order",
        "Transport by truck, about 9 hours driving.",
    ]
    assert lead_hours(texts) == 13
    assert lead_hours(["no times here"]) is None
