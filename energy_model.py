"""Energimodell som använder River för strömningsprediktion."""

from river import compose, linear_model, optim, preprocessing


def build_energy_model():
    return (
        compose.Select("min_temp", "max_temp", "weekday", "wind_strength")
        | preprocessing.StandardScaler()
        | linear_model.LinearRegression(optimizer=optim.SGD(0.01))
    )


def train_energy_model(model=None):
    model = model or build_energy_model()

    training_examples = [
        ({"min_temp": -10.0, "max_temp": 0.0, "weekday": 0, "wind_strength": 12.0}, 32.0),
        ({"min_temp": -5.0, "max_temp": 5.0, "weekday": 1, "wind_strength": 8.0}, 26.0),
        ({"min_temp": 0.0, "max_temp": 10.0, "weekday": 2, "wind_strength": 5.0}, 20.0),
        ({"min_temp": 5.0, "max_temp": 15.0, "weekday": 3, "wind_strength": 3.0}, 15.0),
        ({"min_temp": 10.0, "max_temp": 20.0, "weekday": 4, "wind_strength": 2.0}, 11.0),
        ({"min_temp": 15.0, "max_temp": 25.0, "weekday": 5, "wind_strength": 1.0}, 9.0),
        ({"min_temp": 20.0, "max_temp": 30.0, "weekday": 6, "wind_strength": 0.5}, 7.0),
    ]

    for features, target in training_examples:
        model.learn_one(features, target)

    return model


STREAMING_MODEL = train_energy_model()


def predict_energy(min_temp, max_temp, weekday, wind_strength, volym=750.0):
    prediction = STREAMING_MODEL.predict_one(
        {
            "min_temp": min_temp,
            "max_temp": max_temp,
            "weekday": weekday,
            "wind_strength": wind_strength,
        }
    )
    if prediction is None:
        return 0.0
    return max(0.0, prediction * (volym / 750.0))


def update_model(min_temp, max_temp, weekday, wind_strength, target_kwh):
    STREAMING_MODEL.learn_one(
        {
            "min_temp": min_temp,
            "max_temp": max_temp,
            "weekday": weekday,
            "wind_strength": wind_strength,
        },
        target_kwh,
    )


def remaining_energy_compensation(start_temp, volym=750.0, low_target=25.0, high_target=40.0):
    """Beräkna hur mycket energi som redan finns kvar i tanken och kan kompensera dagens behov."""
    if start_temp <= low_target:
        return 0.0

    useful_temp = min(start_temp, high_target)
    remaining_degrees = useful_temp - low_target
    kwh_per_degree = 0.5
    return remaining_degrees * kwh_per_degree * (volym / 750.0)
