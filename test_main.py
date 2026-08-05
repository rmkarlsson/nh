import datetime
from types import SimpleNamespace

import main


class DummyResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


def test_get_start_temp_returns_float(monkeypatch):
    monkeypatch.setattr(main, "state", SimpleNamespace(get=lambda key: SimpleNamespace(state="27.3")), raising=False)
    assert main.get_start_temp() == 27.3


def test_get_start_temp_missing_sensor(monkeypatch, caplog):
    monkeypatch.setattr(main, "state", SimpleNamespace(get=lambda key: None), raising=False)
    caplog.set_level("ERROR")

    assert main.get_start_temp() is None
    assert "finns inte" in caplog.text


def test_main_selects_cheapest_intervals(monkeypatch, caplog):
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    forecast = [
        {"datetime": tomorrow.isoformat(), "temperature": 5, "wind_speed": 4},
        {"datetime": tomorrow.isoformat(), "temperature": 5, "wind_speed": 4},
    ]

    weather_entity = SimpleNamespace(attributes={"forecast": forecast})
    sensor = SimpleNamespace(state="25.0")
    previous_predicted_entity = SimpleNamespace(state="14.0")

    def get_state(key):
        if key == "sensor.ack_tank_temp":
            return sensor
        if key == "weather.test":
            return weather_entity
        if key == "input_number.previous_predicted":
            return previous_predicted_entity
        return None

    monkeypatch.setattr(main, "state", SimpleNamespace(get=get_state), raising=False)

    prices = [
        {
            "time_start": f"2026-08-05T{hour:02d}:{quarter*15:02d}:00Z",
            "SEK_per_kWh": float(idx),
        }
        for idx, (hour, quarter) in enumerate(((i // 4, i % 4) for i in range(24 * 4)))
    ]

    monkeypatch.setattr(main.requests, "get", lambda url: DummyResponse(200, prices))
    monkeypatch.setattr(main.energy_model, "predict_energy", lambda min_temp, max_temp, weekday, wind_strength, volym=750.0: 6.0)
    monkeypatch.setattr(main.energy_model, "update_model", lambda min_temp, max_temp, weekday, wind_strength, target_kwh: None)
    caplog.set_level("INFO")

    main.main(volym=750.0, elomrade="SE3", weather_entity="weather.test")

    assert "Valda 4 billigaste 15-minutersintervaller" in caplog.text
    assert "2026-08-05T00:00:00Z" in caplog.text
    assert "2026-08-05T00:15:00Z" in caplog.text
    assert "2026-08-05T00:30:00Z" in caplog.text
    assert "2026-08-05T00:45:00Z" in caplog.text


def test_main_uses_previous_run_features_for_feedback(monkeypatch, caplog):
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    forecast = [
        {"datetime": tomorrow.isoformat(), "temperature": 5, "wind_speed": 4},
    ]

    weather_entity = SimpleNamespace(attributes={"forecast": forecast})
    sensor = SimpleNamespace(state="30.0")
    previous_predicted_entity = SimpleNamespace(state="14.0")
    previous_min_temp = SimpleNamespace(state="3.0")
    previous_max_temp = SimpleNamespace(state="7.0")
    previous_weekday = SimpleNamespace(state="2.0")
    previous_wind_strength = SimpleNamespace(state="5.0")

    def get_state(key):
        if key == "sensor.ack_tank_temp":
            return sensor
        if key == "weather.test":
            return weather_entity
        if key == "input_number.previous_predicted":
            return previous_predicted_entity
        if key == "input_number.previous_min_temp":
            return previous_min_temp
        if key == "input_number.previous_max_temp":
            return previous_max_temp
        if key == "input_number.previous_weekday":
            return previous_weekday
        if key == "input_number.previous_wind_strength":
            return previous_wind_strength
        return None

    monkeypatch.setattr(main, "state", SimpleNamespace(get=get_state), raising=False)

    prices = [
        {"time_start": f"2026-08-05T{hour:02d}:{quarter*15:02d}:00Z", "SEK_per_kWh": float(idx)}
        for idx, (hour, quarter) in enumerate(((i // 4, i % 4) for i in range(24 * 4)))
    ]
    monkeypatch.setattr(main.requests, "get", lambda url: DummyResponse(200, prices))

    monkeypatch.setattr(main.energy_model, "predict_energy", lambda min_temp, max_temp, weekday, wind_strength, volym=750.0: 10.0)
    update_calls = []
    def fake_update_model(min_temp, max_temp, weekday, wind_strength, target_kwh):
        update_calls.append({
            "min_temp": min_temp,
            "max_temp": max_temp,
            "weekday": weekday,
            "wind_strength": wind_strength,
            "target_kwh": target_kwh,
        })
    monkeypatch.setattr(main.energy_model, "update_model", fake_update_model)
    monkeypatch.setattr(main.energy_model, "remaining_energy_compensation", lambda start_temp, volym=750.0: 0.0)

    caplog.set_level("INFO")
    main.main(volym=750.0, elomrade="SE3", weather_entity="weather.test")

    assert update_calls == [{
        "min_temp": 3.0,
        "max_temp": 7.0,
        "weekday": 2.0,
        "wind_strength": 5.0,
        "target_kwh": 14.0,
    }]
    assert "Använder energy_model.predict_energy: 10.00 kWh" in caplog.text
    assert "Valda 7 billigaste 15-minutersintervaller" in caplog.text


def test_get_tomorrow_forecast_features(monkeypatch):
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    forecast = [
        {
            "datetime": tomorrow.isoformat(),
            "templow": 3,
            "temperature": 9,
            "wind_speed": 4,
        }
    ]

    weather_entity = SimpleNamespace(attributes={"forecast": forecast})
    monkeypatch.setattr(main, "state", SimpleNamespace(get=lambda key: weather_entity), raising=False)

    features = main.get_tomorrow_forecast_features("weather.test")
    assert features == {
        "min_temp": 3.0,
        "max_temp": 9.0,
        "weekday": tomorrow.weekday(),
        "wind_strength": 4.0,
    }


def test_lookup_daily_energy():
    assert main.lookup_daily_energy(12.0, volym=750.0) == 8.0
    assert main.lookup_daily_energy(6.0, volym=750.0) == 12.0
    assert main.lookup_daily_energy(2.0, volym=750.0) == 18.0
    assert main.lookup_daily_energy(-3.0, volym=750.0) == 24.0
    assert main.lookup_daily_energy(-7.0, volym=750.0) == 30.0
    assert main.lookup_daily_energy(-12.0, volym=750.0) == 36.0


def test_get_previous_predicted(monkeypatch):
    entity = SimpleNamespace(state="14.5")
    monkeypatch.setattr(main, "state", SimpleNamespace(get=lambda key: entity), raising=False)

    assert main.get_previous_predicted("input_number.previous_predicted") == 14.5


def test_remaining_energy_compensation():
    assert main.energy_model.remaining_energy_compensation(25.0, volym=750.0) == 0.0
    assert main.energy_model.remaining_energy_compensation(30.0, volym=750.0) == 2.5
    assert main.energy_model.remaining_energy_compensation(40.0, volym=750.0) == 7.5


def test_main_uses_energy_model_prediction(monkeypatch, caplog):
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    forecast = [
        {"datetime": tomorrow.isoformat(), "temperature": 5, "wind_speed": 4},
        {"datetime": tomorrow.isoformat(), "temperature": 5, "wind_speed": 4},
    ]

    weather_entity = SimpleNamespace(attributes={"forecast": forecast})
    sensor = SimpleNamespace(state="25.0")
    previous_predicted_entity = SimpleNamespace(state="14.0")

    def get_state(key):
        if key == "sensor.ack_tank_temp":
            return sensor
        if key == "weather.test":
            return weather_entity
        if key == "input_number.previous_predicted":
            return previous_predicted_entity
        return None

    monkeypatch.setattr(main, "state", SimpleNamespace(get=get_state), raising=False)

    prices = [
        {"time_start": f"2026-08-05T{hour:02d}:{quarter*15:02d}:00Z", "SEK_per_kWh": float(idx)}
        for idx, (hour, quarter) in enumerate(((i // 4, i % 4) for i in range(24 * 4)))
    ]
    monkeypatch.setattr(main.requests, "get", lambda url: DummyResponse(200, prices))
    monkeypatch.setattr(main.energy_model, "predict_energy", lambda min_temp, max_temp, weekday, wind_strength, volym=750.0: 15.0)
    monkeypatch.setattr(main.energy_model, "update_model", lambda min_temp, max_temp, weekday, wind_strength, target_kwh: None)
    monkeypatch.setattr(main.energy_model, "remaining_energy_compensation", lambda start_temp, volym=750.0: 1.0)
    caplog.set_level("INFO")

    main.main(volym=750.0, elomrade="SE3", weather_entity="weather.test")

    assert "Använder energy_model.predict_energy: 15.00 kWh" in caplog.text
    assert "Ingen tidigare använda features hittades, uppdaterar med dagens features och feedback-mål" in caplog.text
    assert "Justering 2: kvarvarande energi i tanken motsvarar 1.00 kWh, slutligt behov blir 14.00 kWh" in caplog.text
    assert "Valda" in caplog.text
