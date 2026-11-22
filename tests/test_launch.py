"""Packing clock: shifts from the plan workbook, night gaps skipped, packed = newest figure."""

from datetime import datetime
from zoneinfo import ZoneInfo

import openpyxl

from ask_soony import launch

TZ = ZoneInfo("Europe/Berlin")


def make_portal(tmp_path, packed_status="60.300"):
    soony = tmp_path / "soony"
    (soony / "master").mkdir(parents=True)
    (soony / "context").mkdir()
    (soony / "erp").mkdir()
    (soony / "master" / "products.csv").write_text(
        "product_id,name,lifecycle,launch_date,day_one_qty\n"
        "P-A,Thing – Big Edition,launch,2030-06-02,1000\n"
        "P-OLD,Old,running,2020-01-01,\n"
    )
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Editions"
    ws.append(["Plan · confidential"])
    ws.append(["Edition", "Product", "Day-one demand", "Packed", "Rate per hour"])
    ws.append(["Big", "P-A", 1000, 100, 50])
    sh = wb.create_sheet("Shifts")
    sh.append(["Day", "Shift", "Hours"])
    sh.append(["Saturday", "06:00–22:00", 16])
    sh.append(["Sunday", "06:00–20:00", 14])
    wb.save(soony / "context" / "launch_plan_X.xlsx")
    st = openpyxl.Workbook()
    fg = st.active
    fg.append(["Packed copies, later"])
    fg.append(["Site", "Material", "Qty"])
    fg.append(["DC", "P-A", packed_status])
    st.save(soony / "erp" / "stock_update_X.xlsx")
    return tmp_path


def test_windows_and_packed_from_any_source(tmp_path):
    clock = launch.load(make_portal(tmp_path, "400"), TZ)
    assert [(s.hour, e.hour) for s, e in clock.windows] == [(6, 22), (6, 20)]
    assert clock.windows[1][0].date().isoformat() == "2030-06-02"  # Sunday = launch day
    ed = clock.editions["P-A"]
    assert (ed.rate_per_hour, ed.packed, ed.remaining) == (50, 400, 600)


def test_line_never_stops_at_night(tmp_path):
    clock = launch.load(make_portal(tmp_path, "400"), TZ)
    sat_21 = datetime(2030, 6, 1, 21, 0, tzinfo=TZ)
    # 600 left at 50/h; 300 short -> 300 packable = 6 h: 1 h on Saturday, 5 h from Sunday 06:00
    stops = clock.line_stops(sat_21, "P-A", 300)
    assert stops == datetime(2030, 6, 2, 11, 0, tzinfo=TZ)
    # at night, nothing short is still fine, and slack counts only shift hours
    night = datetime(2030, 6, 2, 1, 0, tzinfo=TZ)
    assert clock.hours_left(night) == 14
    assert clock.slack_hours(night, "P-A") == 2.0
    assert clock.line_stops(night, "P-A", 0) is None
