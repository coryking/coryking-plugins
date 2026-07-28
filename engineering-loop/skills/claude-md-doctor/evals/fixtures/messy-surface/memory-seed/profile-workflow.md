---
name: profile-render-workflow
description: Step-by-step procedure for rendering a plot from a machine profile plus a paper profile
metadata:
  type: project
  modified: 2026-06-18T11:02:47.000Z
---

Rendering a plot is a fixed six-step sequence. Getting the order wrong wedges
the bridge and needs a service restart.

1. Stop the plot server before editing any profile:

   ```bash
   systemctl --user stop plotbot.service
   ```

2. Edit the machine profile under `profiles/machine/` and the paper profile
   under `profiles/`.

3. Validate the pair — this catches travel-limit violations before the pen
   is anywhere near the paper:

   ```bash
   ./tools/validate-profile.py profiles/machine/plotbot-main.ini profiles/draft-fast.ini
   ```

4. Restart the server:

   ```bash
   systemctl --user start plotbot.service
   ```

5. Dry-run with the pen up to confirm the travel envelope:

   ```bash
   ./tools/plot.py --dry-run --machine plotbot-main --paper draft-fast in.svg
   ```

6. Run it for real, dropping the feed rate first if the stock is hot-press.

If step 3 reports a travel-limit violation, fix the paper profile rather than
widening the machine profile — the machine limits are physical.
