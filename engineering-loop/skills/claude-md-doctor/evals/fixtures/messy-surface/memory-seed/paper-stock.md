---
name: paper-stock-behavior
description: How each paper stock in the workshop behaves under the plotter — feed rate, drying, pen pairing, and storage
metadata:
  type: reference
  modified: 2026-07-02T10:12:05.000Z
---

Everything learned about how the workshop's paper stocks behave on the plotter.
Long by design: it is a topic file, so none of this loads at session start.

## 200gsm cold-press (the default)

Stored in the flat file under the bench. This is the stock to reach for unless
a piece is going into a frame.

- Feeds at any speed the machine will do. No smearing at the leading edge.
- Surface has enough tooth that fineliners skip if the pen is dragged too fast
  across a long horizontal run. Break long horizontals into segments.
- Takes water-based ink without cockling as long as fills stay under about
  40% coverage. Past that the sheet ripples and never fully flattens.
- Cut edges fuzz. Trim after plotting, not before.

## 300gsm hot-press (framed series only)

- The leading edge lifts off the platen at full feed rate and smears the first
  pass. Drop the feed rate for the first 20mm of travel, then it is stable.
- Slick surface, so ink sits on top and stays wet noticeably longer. Leave a
  sheet flat for a few minutes before handling.
- Pairs badly with the broad markers — they pool at direction changes.
- Expensive enough that a dry-run pass is always worth the time.

## Newsprint (test stock)

- Only used for envelope checks. Tears if the pen pressure goes above about
  30%, so it is not a valid test for pressure-sensitive work.
- Feed it in short sheets; long sheets skew because the surface has no grip.

## Bristol (experimental)

- Two sheets tried so far. Behaves like hot-press but flatter and it does not
  need the leading-edge feed drop.
- Not enough evidence yet to make it the framed-series default.

## Storage

The flat file stays closed. Humidity swings in the workshop are enough to make
cold-press curl at the corners within a week of being left out, and a curled
sheet catches the pen carriage on the return travel.

## Pen pairing summary

| Stock | Fineliner | Broad marker | Brush pen |
|-------|-----------|--------------|-----------|
| 200gsm cold-press | good | good | acceptable |
| 300gsm hot-press | good | pools at corners | poor |
| Newsprint | tears | acceptable | poor |
| Bristol | good | good | untested |

## Reordering

Same supplier as the last order. The cold-press SKU changed once and the
replacement had a different tooth; check the sample sheet before committing to
a full box.
