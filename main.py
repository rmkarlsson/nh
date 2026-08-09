import datetime
import logging

import requests

try:
  from appdaemon.plugins.hass.hassapi import Hass
except Exception:
  class Hass:
    pass

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


class EnergyPriceApp(Hass):
  def __init__(self, ad=None, name=None, *args, **kwargs):
    self.ad = ad
    self.active_entity = kwargs.pop("active_entity", "input_boolean.heating_active")
    self.name = name or "energy_price_app"
    if ad is None:
      super().__init__(*args, **kwargs)
      self.name = name or self.__class__.__name__
    else:
      self.initialize()

  def initialize(self):
    if self.ad is not None:
      self.ad.run_daily(self._run, datetime.time(23, 55, 0))
    else:
      self.run_daily(self._run, datetime.time(23, 55, 0))

  def _run(self, *args, **kwargs):
    main(app=self, active_entity=self.active_entity)

  def schedule_activation(self, start_dt, end_dt, active_entity="switch.0xf0fd45fffeef4457"):
    # AccTankKontaktor == 0xf0fd45fffeef4457
    entity_id = active_entity or self.active_entity
    if entity_id is None:
      return
    self.run_at(self._activate_entity, start_dt, entity_id=entity_id)
    self.run_at(self._deactivate_entity, end_dt, entity_id=entity_id)

  def _activate_entity(self, **kwargs):
    entity_id = kwargs.get("entity_id")
    self._set_entity_state(entity_id, True)

  def _deactivate_entity(self, **kwargs):
    entity_id = kwargs.get("entity_id")
    self._set_entity_state(entity_id, False)

  def _set_entity_state(self, entity_id, active):
    if entity_id is None:
      return
    if hasattr(self, "set_state") and callable(self.set_state):
      self.set_state(entity_id, state="on" if active else "off")
    else:
      logger.info("Sätter %s till %s", entity_id, "på" if active else "av")


def parse_period(value):
  if isinstance(value, datetime.datetime):
    return value
  if not isinstance(value, str):
    return None

  try:
    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError:
    return None

  if parsed.tzinfo is not None:
    parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
  return parsed


def main(elomrade="SE2", active_entity="input_boolean.heating_active", app=None):
  today = datetime.date.today()
  tomorrow = today + datetime.timedelta(days=1)
  year = tomorrow.strftime("%Y")
  month_day = tomorrow.strftime("%m-%d")

  url = f"https://www.elprisetjustnu.se/api/v1/prices/{year}/{month_day}_{elomrade}.json"
  response = requests.get(url)

  if response.status_code != 200:
    logger.error(f"Fel vid hämtning: {response.status_code}")
    return

  prices = response.json()
  cheapest = sorted(prices, key=lambda item: item["SEK_per_kWh"])[:12]

  logger.info("De 12 billigaste 15-minutersperioderna:")
  for item in cheapest:
    logger.info(f"{item['time_start']}: {item['SEK_per_kWh']} kr/kWh")

    if app is not None and hasattr(app, "schedule_activation"):
      start_dt = parse_period(item.get("time_start"))
      end_dt = parse_period(item.get("time_end"))
      if start_dt is None:
        continue
      if end_dt is None:
        end_dt = start_dt + datetime.timedelta(minutes=15)
      app.schedule_activation(start_dt, end_dt, active_entity)


if __name__ == "__main__":
  main()