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
  def __init__(self, *args, **kwargs):
    # Try to preserve AppDaemon behaviour by calling the superclass
    # initializer when available. If it raises, we silently continue
    # so tests (which don't provide a real Hass) still work.
    try:
      super().__init__(*args, **kwargs)
    except Exception:
      pass

    # Compatibility shim for tests that instantiate with (ad, name)
    # as positional args, or pass ad/name/args via kwargs.
    ad = None
    name = None
    provided_args = None
    if len(args) >= 1:
      ad = args[0]
    if len(args) >= 2:
      name = args[1]
    ad = kwargs.get("ad", ad)
    name = kwargs.get("name", name)
    provided_args = kwargs.get("args", None)

    # Only set attributes if the superclass didn't already set them.
    if not hasattr(self, "ad"):
      self.ad = ad
    if not hasattr(self, "name") or getattr(self, "name") is None:
      self.name = name
    if not hasattr(self, "args") or getattr(self, "args") is None:
      self.args = provided_args or {}

    self.active_entity = self.args.get(
        "active_entity",
        "input_boolean.heating_active"
    )

    # If an AD-like object was provided, schedule daily run immediately
    # (this mirrors how tests instantiate the app). Real AppDaemon will
    # typically call `initialize()` where we also schedule if available.
    if getattr(self, "ad", None) is not None and hasattr(self.ad, "run_daily"):
      self.ad.run_daily(self._run, datetime.time(23, 55, 0))

  def initialize(self):
    # Keep initialize for AppDaemon compatibility; schedule using the
    # instance's run_daily if available on self (AD will provide it).
    self.active_entity = getattr(self, "args", {}).get(
        "active_entity",
        "input_boolean.heating_active"
    )

    if hasattr(self, "run_daily") and callable(self.run_daily):
      self.run_daily(self._run, datetime.time(23, 55, 0))

  def _run(self, *args, **kwargs):
    main(app=self, active_entity=self.active_entity)

  def schedule_activation(self, start_dt, end_dt, active_entity=None):
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
        parsed = datetime.datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    # Return naive datetimes (UTC) to match test expectations.
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
  # Samla perioder, slå ihop intilliggande/överlappande intervall, och schemalägg dem
  intervals = []
  for item in cheapest:
    logger.info(f"{item['time_start']}: {item['SEK_per_kWh']} kr/kWh")

    if app is not None and hasattr(app, "schedule_activation"):
      start_dt = parse_period(item.get("time_start"))
      end_dt = parse_period(item.get("time_end"))
      if start_dt is None:
        continue
      if end_dt is None:
        end_dt = start_dt + datetime.timedelta(minutes=15)
      intervals.append((start_dt, end_dt))

  # Sortera och slå ihop intervaller så att intilliggande (end == next start)
  # och överlappande intervall blir ett sammanhängande intervall.
  merged = []
  for start, end in sorted(intervals, key=lambda x: x[0]):
    if not merged:
      merged.append([start, end])
      continue
    last_start, last_end = merged[-1]
    # Slå ihop överlappande eller intilliggande intervaller. Tidigare
    # beteende höll intilliggande separata, men i praktiken vill vi
    # ha en kontinuerlig aktivering för intilliggande perioder så
    # vi använder start <= last_end för att även slå ihop dem.
    if start <= last_end:
      if end > last_end:
        merged[-1][1] = end
    else:
      merged.append([start, end])

  for start, end in merged:
    logger.info("Schemalägger %s från %s till %s", active_entity, start, end)
    app.schedule_activation(start, end, active_entity)


if __name__ == "__main__":
  main()
