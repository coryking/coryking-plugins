---
name: controller-host-address
description: Address and port for the plotbot controller host and its serial-over-TCP bridge
metadata:
  type: reference
  modified: 2026-01-09T08:44:02.000Z
---

The plot server runs on `plotbot.local`. The serial-over-TCP bridge listens on
port 8888, so a plot is kicked off by opening a socket to `plotbot.local:8888`.
