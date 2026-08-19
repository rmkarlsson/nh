import datetime

import main


class DummyResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


def test_energy_price_app_initializes_daily_schedule(monkeypatch):
    class DummyAd:
        def __init__(self):
            self.scheduled = []

        def run_daily(self, callback, time, **kwargs):
            self.scheduled.append((callback, time, kwargs))

    called = []

    def fake_main(*args, **kwargs):
        called.append((args, kwargs))

    monkeypatch.setattr(main, "main", fake_main)
    ad = DummyAd()

    app = main.EnergyPriceApp(ad, "energy_app")

    assert len(ad.scheduled) == 1
    callback, scheduled_time, kwargs = ad.scheduled[0]
    assert scheduled_time == datetime.time(23, 55, 0)
    assert kwargs == {}

    callback()
    assert called[0][0] == ()
    assert called[0][1]["app"] is app
    assert called[0][1]["active_entity"] == "input_boolean.heating_active"
    assert app.name == "energy_app"


def test_main_selects_12_cheapest_intervals(monkeypatch, caplog):
    prices = [
        {"time_start": f"2026-08-05T{hour:02d}:{quarter * 15:02d}:00Z", "SEK_per_kWh": float(idx)}
        for idx, (hour, quarter) in enumerate(((i // 4, i % 4) for i in range(24 * 4)))
    ]

    monkeypatch.setattr(main.requests, "get", lambda url: DummyResponse(200, prices))
    caplog.set_level("INFO")

    main.main(elomrade="SE3")

    assert "De 12 billigaste 15-minutersperioderna" in caplog.text
    assert "2026-08-05T00:00:00Z" in caplog.text
    assert "2026-08-05T00:15:00Z" in caplog.text
    assert "2026-08-05T00:30:00Z" in caplog.text
    assert "2026-08-05T00:45:00Z" in caplog.text


def test_main_schedules_activation_for_selected_periods(monkeypatch):
    prices = [
        {"time_start": "2026-08-05T00:00:00Z", "time_end": "2026-08-05T00:15:00Z", "SEK_per_kWh": 1.0},
        {"time_start": "2026-08-05T00:15:00Z", "time_end": "2026-08-05T00:30:00Z", "SEK_per_kWh": 2.0},
    ]

    class DummyApp:
        def __init__(self):
            self.scheduled = []

        def schedule_activation(self, start_dt, end_dt, active_entity):
            self.scheduled.append((start_dt, end_dt, active_entity))

    monkeypatch.setattr(main.requests, "get", lambda url: DummyResponse(200, prices))
    app = DummyApp()

    main.main(elomrade="SE3", app=app, active_entity="input_boolean.heating_active")

    # Efter ändring: intilliggande 15-minutersperioder slås ihop till
    # en kontinuerlig aktivering. De två perioderna ovan blir ett intervall.
    assert len(app.scheduled) == 1
    assert app.scheduled[0][2] == "input_boolean.heating_active"
    assert app.scheduled[0][0] == datetime.datetime(2026, 8, 5, 0, 0, 0)
    assert app.scheduled[0][1] == datetime.datetime(2026, 8, 5, 0, 30, 0)


def test_main_merges_12_quarters_into_single_3h_activation(monkeypatch):
    # Skapa 12 intilliggande 15-minutersperioder (totalt 3 timmar).
    # Bygg start- och end-tid genom att addera 15-minuterssteg så att
    # timövergångar hanteras korrekt.
    base = datetime.datetime(2026, 8, 5, 0, 0, tzinfo=datetime.timezone.utc)
    prices = []
    for i in range(12):
        start_dt = base + datetime.timedelta(minutes=15 * i)
        end_dt = start_dt + datetime.timedelta(minutes=15)
        # Gör priserna varierande: dyrast först, billigast sist.
        price = float(12 - i)
        prices.append({
            "time_start": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "time_end": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "SEK_per_kWh": price,
        })

    class DummyApp:
        def __init__(self):
            self.scheduled = []

        def schedule_activation(self, start_dt, end_dt, active_entity):
            self.scheduled.append((start_dt, end_dt, active_entity))

    monkeypatch.setattr(main.requests, "get", lambda url: DummyResponse(200, prices))
    app = DummyApp()

    main.main(elomrade="SE3", app=app, active_entity="input_boolean.heating_active")

    assert len(app.scheduled) == 1
    start, end, entity = app.scheduled[0]
    assert entity == "input_boolean.heating_active"
    assert end - start == datetime.timedelta(hours=3)
