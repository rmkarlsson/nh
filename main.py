import datetime
import logging
import math
from types import SimpleNamespace

import requests
import energy_model

try:
  from appdaemon.plugins.hass.hassapi import Hass
except Exception:
  class Hass:
    pass

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)
state = None


class AppDaemonState:
  def __init__(self, app):
    self.app = app

  def get(self, entity_id):
    entity = self.app.get_state(entity_id)
    if entity is None:
      return None
    if hasattr(entity, "state") and hasattr(entity, "attributes"):
      return entity
    if isinstance(entity, dict):
      attrs = entity.get("attributes", {}) or {}
      if not hasattr(attrs, "get"):
        attrs = dict(attrs)
      return SimpleNamespace(state=entity.get("state"), attributes=SimpleNamespace(**attrs))
    return entity


class EnergyModelApp(Hass):
  def __init__(self, ad=None, name=None, *args, **kwargs):
    self.ad = ad
    self.name = name or "energy_model"
    if ad is None:
      super().__init__(*args, **kwargs)
      self.name = name or self.__class__.__name__
    else:
      self.initialize()

  def initialize(self):
    if self.ad is not None:
      self.ad.run_daily(self._run, datetime.time(18, 0, 0))
    else:
      self.run_daily(self._run, datetime.time(18, 0, 0))

  def _run(self, *args, **kwargs):
    global state
    if hasattr(self, "get_state") and callable(self.get_state):
      state = AppDaemonState(self)
    main()


def get_state_entity(entity_id):
  if state is None:
    logger.error("Ingen state-anslutning tillgänglig")
    return None
  return state.get(entity_id)


def get_start_temp():
  sensor = get_state_entity("sensor.ack_tank_temp")
  if sensor is None:
    logger.error("Sensor sensor.ack_tank_temp finns inte")
    return None
  if sensor.state in ("unknown", "unavailable", "None", ""):
    logger.error(f"Sensor ack-tank-temp har ogiltigt värde: {sensor.state}")
    return None
  try:
    return float(sensor.state)
  except ValueError:
    logger.error(f"Kunde inte tolka sensorvärdet som temperatur: {sensor.state}")
    return None


def parse_timestamp(value):
  if isinstance(value, datetime.datetime):
    return value
  if not isinstance(value, str):
    return None

  try:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError:
    try:
      return datetime.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
      return None


def get_tomorrow_forecast_features(weather_entity_id="weather.home"):
  entity = get_state_entity(weather_entity_id)
  if entity is None:
    logger.error(f"Weather entity {weather_entity_id} finns inte")
    return None

  forecast = getattr(entity.attributes, "forecast", None)
  if forecast is None:
    forecast = entity.attributes.get("forecast") if hasattr(entity, "attributes") else None
  if not forecast:
    logger.error(f"Forecast saknas i {weather_entity_id}")
    return None

  def value_from(item, *keys):
    for key in keys:
      value = item.get(key)
      if value is not None:
        return value
    return None

  tomorrow = datetime.date.today() + datetime.timedelta(days=1)
  temps = []
  min_temps = []
  max_temps = []
  wind_strengths = []

  for item in forecast:
    dt = parse_timestamp(item.get("datetime") or item.get("time") or item.get("timestamp") or item.get("from"))
    if dt is None or dt.date() != tomorrow:
      continue

    temp = value_from(item, "temperature", "temp", "temp_high", "temperature_high", "max_temp", "temperature_max", "high")
    templow = value_from(item, "templow", "temp_low", "min_temp", "temperature_min", "low")
    temphigh = value_from(item, "temp_high", "temperature_high", "max_temp", "temperature_max", "high")
    wind = value_from(item, "wind_speed", "wind_strength", "wind", "wind_power", "windPower")

    if temp is not None:
      try:
        temps.append(float(temp))
      except (TypeError, ValueError):
        pass

    if templow is not None:
      try:
        min_temps.append(float(templow))
      except (TypeError, ValueError):
        pass

    if temphigh is not None:
      try:
        max_temps.append(float(temphigh))
      except (TypeError, ValueError):
        pass

    if wind is not None:
      try:
        wind_strengths.append(float(wind))
      except (TypeError, ValueError):
        pass

  if not temps and not (min_temps and max_temps):
    logger.error(f"Ingen temperaturdata för morgondagen hittades i {weather_entity_id}")
    return None

  if not wind_strengths:
    logger.error(f"Ingen vindstyrkedata för morgondagen hittades i {weather_entity_id}")
    return None

  min_temp = min(min_temps) if min_temps else min(temps)
  max_temp = max(max_temps) if max_temps else max(temps)
  wind_strength = sum(wind_strengths) / len(wind_strengths)
  weekday = tomorrow.weekday()

  return {
    "min_temp": min_temp,
    "max_temp": max_temp,
    "weekday": weekday,
    "wind_strength": wind_strength,
  }


def lookup_daily_energy(mean_temp, volym=750.0):
  if mean_temp >= 10:
    energy_for_750 = 8.0
  elif mean_temp >= 5:
    energy_for_750 = 12.0
  elif mean_temp >= 0:
    energy_for_750 = 18.0
  elif mean_temp >= -5:
    energy_for_750 = 24.0
  elif mean_temp >= -10:
    energy_for_750 = 30.0
  else:
    energy_for_750 = 36.0

  energy = energy_for_750 * (volym / 750.0)
  logger.info(f"Lookup: medeltemp {mean_temp:.1f}°C => {energy:.2f} kWh för {volym:.0f} liter")
  return energy


def get_previous_predicted(previous_predicted_entity="input_number.previous_predicted"):
  entity = get_state_entity(previous_predicted_entity)
  if entity is None:
    logger.info(f"Ingen tidigare prediktion hittades i {previous_predicted_entity}")
    return None
  if entity.state in ("unknown", "unavailable", "None", ""):
    logger.warning(f"Tidigare prediktion {previous_predicted_entity} har ogiltigt värde: {entity.state}")
    return None
  try:
    return float(entity.state)
  except ValueError:
    logger.error(f"Kunde inte tolka {previous_predicted_entity} som tidigare prediktion: {entity.state}")
    return None


def get_previous_training_features(prefix="input_number.previous"):
  keys = {
    "min_temp": f"{prefix}_min_temp",
    "max_temp": f"{prefix}_max_temp",
    "weekday": f"{prefix}_weekday",
    "wind_strength": f"{prefix}_wind_strength",
  }
  features = {}
  for name, entity_id in keys.items():
    entity = get_state_entity(entity_id)
    if entity is None:
      return None
    if getattr(entity, "state", None) in ("unknown", "unavailable", "None", ""):
      return None
    try:
      features[name] = float(entity.state)
    except (TypeError, ValueError):
      return None
  return features


def get_previous_run_data(previous_predicted_entity="input_number.previous_predicted", prefix="input_number.previous"):
  previous_predicted = get_previous_predicted(previous_predicted_entity)
  previous_features = get_previous_training_features(prefix)
  if previous_predicted is None or previous_features is None:
    return None
  return {
    "predicted_kwh": previous_predicted,
    "features": previous_features,
  }


def get_feedback_target(previous_predicted, start_temp, volym=750.0):
  if start_temp < 25.0:
    correction_kwh = (25.0 - start_temp) * 0.5 * (volym / 750.0)
    logger.warning("Starttemperatur under 25°C visar att tidigare schema gav för lite energi")
    return previous_predicted + correction_kwh
  if start_temp > 40.0:
    correction_kwh = (start_temp - 40.0) * 0.5 * (volym / 750.0)
    logger.warning("Starttemperatur över 40°C visar att tidigare schema gav för mycket energi")
    return max(0.0, previous_predicted - correction_kwh)
  return previous_predicted


def main(volym=750.0, elomrade="SE2", weather_entity="weather.home", previous_predicted_entity="input_number.previous_predicted"):
  start_temp = get_start_temp()
  if start_temp is None:
    return

  previous_predicted = get_previous_predicted(previous_predicted_entity)
  previous_training_features = get_previous_training_features("input_number.previous")
  previous_run = None
  if previous_predicted is not None and previous_training_features is not None:
    previous_run = {
      "predicted_kwh": previous_predicted,
      "features": previous_training_features,
    }

  if start_temp < 25.0:
    logger.warning(f"Starttemperatur {start_temp:.1f}°C är under låg målnivå; tidigare estimering var för låg")
  elif start_temp > 40.0:
    logger.warning(f"Starttemperatur {start_temp:.1f}°C är över hög målnivå; tidigare estimering var för hög")
  else:
    logger.info(f"Starttemperatur {start_temp:.1f}°C är inom rimligt målintervall")

  imorgon = datetime.date.today() + datetime.timedelta(days=1)
  ar = imorgon.strftime("%Y")
  manad_dag = imorgon.strftime("%m-%d")

  url = f"https://www.elprisetjustnu.se/api/v1/prices/{ar}/{manad_dag}_{elomrade}.json"
  svar = requests.get(url)

  if svar.status_code != 200:
    logger.error(f"Fel vid hämtning: {svar.status_code}")
    return

  priser = svar.json()

  effekt = 6.0

  features = get_tomorrow_forecast_features(weather_entity)
  if features is None:
    logger.error(f"Failed to get forecast features for {weather_entity}")
    return

  predicted_kwh = energy_model.predict_energy(
    min_temp=features["min_temp"],
    max_temp=features["max_temp"],
    weekday=features["weekday"],
    wind_strength=features["wind_strength"],
    volym=volym,
  )
  logger.info(f"\nAnvänder energy_model.predict_energy: {predicted_kwh:.2f} kWh")

  if previous_run is not None:
    previous_predicted = previous_run["predicted_kwh"]
    previous_features = previous_run["features"]
    feedback_target = get_feedback_target(previous_predicted, start_temp, volym=volym)
    energy_model.update_model(
      min_temp=previous_features["min_temp"],
      max_temp=previous_features["max_temp"],
      weekday=previous_features["weekday"],
      wind_strength=previous_features["wind_strength"],
      target_kwh=feedback_target,
    )
    adjusted_kwh = predicted_kwh
    logger.info(f"Uppdaterar River-modellen med gårdagens använda features och föregående prognos {previous_predicted:.2f} kWh som feedback-mål: {feedback_target:.2f} kWh")
  elif previous_predicted is not None:
    feedback_target = get_feedback_target(previous_predicted, start_temp, volym=volym)
    energy_model.update_model(
      min_temp=features["min_temp"],
      max_temp=features["max_temp"],
      weekday=features["weekday"],
      wind_strength=features["wind_strength"],
      target_kwh=feedback_target,
    )
    adjusted_kwh = predicted_kwh
    logger.info("Ingen tidigare använda features hittades, uppdaterar med dagens features och feedback-mål")
  else:
    adjusted_kwh = predicted_kwh
    logger.info("Ingen tidigare prediktion tillgänglig för online-träning")

  remaining_kwh = energy_model.remaining_energy_compensation(start_temp, volym=volym)
  energi_kwh = max(0.0, adjusted_kwh - remaining_kwh)
  logger.info(f"Justering 2: kvarvarande energi i tanken motsvarar {remaining_kwh:.2f} kWh, slutligt behov blir {energi_kwh:.2f} kWh")

  timmar_uppvarmning = energi_kwh / effekt
  intervall_per_timme = 4
  intervaller_energi = math.ceil(timmar_uppvarmning * intervall_per_timme)
  logger.info(f"Tid med {effekt:.1f} kW: {timmar_uppvarmning:.2f} timmar => {intervaller_energi} x 15 min-intervaller")

  valda_intervaller = sorted(priser, key=lambda p: p['SEK_per_kWh'])[:intervaller_energi]
  logger.info(f"\nValda {intervaller_energi} billigaste 15-minutersintervaller för att nå energibehovet:")
  for p in valda_intervaller:
    logger.info(f"{p['time_start']}: {p['SEK_per_kWh']} kr/kWh")

  billigaste = sorted(priser, key=lambda p: p['SEK_per_kWh'])[:12]
  logger.info(f"\nDe billigaste 3 timmarna för imorgon (12 x 15 min):")
  for p in billigaste:
    logger.info(f"{p['time_start']}: {p['SEK_per_kWh']} kr/kWh")


if __name__ == "__main__":
  main()