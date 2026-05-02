"""TOU rates module — kept on the simulator side after the energy-system extraction.

The rest of the energy/ package (system, components, bus, types) was lifted into
``ebus_emitter.scheduleRunner.energy``. ``tou.py`` stays because rate-driven
optimisation is a producer-side modelling concern; the dashboard's TOU display
imports ``all_rates_for_day`` from here."""
