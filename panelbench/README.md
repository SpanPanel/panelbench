# SPAN PanelBench

Simulates a SPAN electrical panel for testing and upgrade modeling.

Publishes Homie v5 MQTT topics, serves an eBus bootstrap API, and
advertises via mDNS — exactly like real hardware. No real SPAN panel
is needed.

Clone your real panel, replay recorder data, and model the impact of
adding solar or battery storage.

Install this app from Home Assistant's App Store by adding the
repository URL as a third-party app repository. This app is not
distributed through HACS.

This App emulates SPAN firmware `r202633+`, the first to publish the
parent/child device tree (`data-model-version` `1.x`).

Reading that tree needs `span-panel-api` v3.0.1 or later. The
SpanPanel/span integration carries it from v2.1.0b13 onward; earlier
releases, v2.0.8 included, pin `span-panel-api` 2.6.4 and read the flat
schema only.

Visit [SPAN PanelBench](https://github.com/SpanPanel/panelbench)
page for more details.
