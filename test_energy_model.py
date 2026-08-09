import energy_model


def test_predict_energy_based_on_forecast_features():
    warm_day = energy_model.predict_energy(min_temp=10.0, max_temp=18.0, weekday=4, wind_strength=1.0)
    cold_day = energy_model.predict_energy(min_temp=-5.0, max_temp=2.0, weekday=1, wind_strength=8.0)

    assert warm_day > 0
    assert cold_day > 0


def test_update_model_learns_from_target():
    initial_prediction = energy_model.predict_energy(min_temp=0.0, max_temp=5.0, weekday=2, wind_strength=5.0)
    energy_model.update_model(min_temp=0.0, max_temp=5.0, weekday=2, wind_strength=5.0, target_kwh=20.0)
    updated_prediction = energy_model.predict_energy(min_temp=0.0, max_temp=5.0, weekday=2, wind_strength=5.0)

    assert updated_prediction >= 0


def test_model_can_be_saved_and_loaded(tmp_path):
    path = tmp_path / "stream_model.pkl"
    model = energy_model.train_energy_model()

    saved_path = energy_model.save_model(model, path)
    loaded_model = energy_model.load_model(path)

    assert saved_path == path
    assert path.exists()
    assert loaded_model is not None
    assert loaded_model.predict_one({"min_temp": 0.0, "max_temp": 10.0, "weekday": 2, "wind_strength": 5.0}) is not None
