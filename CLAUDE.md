# Fleet Operations

Tools the Ford of Murfreesboro Fleet Department uses to do business: quoting, billing, and (in progress) the inventory and order dashboard. Jason, the Fleet Director, is not a developer and is often on an iPad. Talk about vehicles, orders and customers, not files or code, unless he asks.

## Design before building (grill-me)

Matt Pocock's `grill-me` skill is installed in `.claude/skills/` (`grill-me` is the command, `grilling` holds the method; MIT, from github.com/mattpocock/skills).

- Before building a new tool, screen or data feed, run `/grill-me` on the plan and do not start building until Jason confirms the shared understanding.
- Look facts up yourself (this repo, the data exports, the knowledge base in the `fleet-marketing-agent` repo); only put decisions to Jason.
- Keep rounds short enough to answer from an iPad: plain language, a recommended answer on every question, worded so "yes" accepts it.

## Data

Inventory and order exports contain customer names and invoice cost. Never commit them or anything built from them.
