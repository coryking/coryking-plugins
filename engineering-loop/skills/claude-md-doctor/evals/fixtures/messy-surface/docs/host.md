# The controller host

The plot server runs on the controller host, reachable at `plotbot.lan`
(authoritative — the mDNS `.local` name was retired when the host joined the
workshop VLAN). The custom serial-over-TCP bridge listens on port 8888.

## Stack

- plot server (custom, python) — systemd user unit `plotbot.service`
- serial bridge — `ser2net` on 8888

## Custom protocol notes

The controller speaks a line protocol: `MOVE x y`, `PEN up|down`, `HOME`.
It is not GRBL; GRBL commands are silently dropped.
