# Fleet_operations
Repo for all of the tools that our fleet department needs to do business effectively

## Inventory dashboard

`/inventory` in the fleet app: one row per vehicle from order through sale.

- **Weekly upload** (Upload tab): Ford Pro order status, Ford inventory, Tess's list and the Ford sold-VIN report. Files are recognised by their column headings; anything not uploaded keeps its last version. The reps' old spreadsheet can be loaded once to seed status, customer, upfitter and notes.
- **Edited in the app**: status, customer, upfitter, upfit complete and notes, one vehicle or many at once. Every change is stamped with who and when.
- **Needs attention**: disagreements between sources and units nobody has claimed.
- Data lives in `inventory/` under `FLEETQUOTE_DATA` and is never committed. Invoice cost is not read.
- "On ground" means Ford's inventory lists the unit as unsold stock or it is on Tess's list. Ford Pro "Delivered" alone does not count.

Logic is in `app/lib/inventory.py`; the page is `app/static/inventory.html`.
