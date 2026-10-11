"""Render quote.html with sample data to sample.pdf (for visual checks).
Usage: python3 sample.py   (needs jinja2 + playwright)"""
from pathlib import Path
from jinja2 import Template
from playwright.sync_api import sync_playwright

here = Path(__file__).parent
data = dict(
    quote_id="Q-2027-0001", quote_date="09/27/2026",
    customer=dict(organization="Southeast Tennessee Development District",
                  contact="Brenna Bak, Senior Project Administrator", fin_code="KS280"),
    rep=dict(name="Craig Baton", title="Fleet Sales Manager",
             email="cbaton@fordofmurfreesboro.com", phone="(615) 243-1528"),
    vehicles=[dict(line=1, description="2027 F-250 4x4 SD Super Cab 8' box 164\" WB SRW XL (X2B)",
                   spec="Order Code 600A | 7.3L 2V DEVCT NA PFI V8 Gas | Oxford White", price="$46,432.78")],
    options=[dict(description="XL Chrome Package", code="(96V)", price="$425.00")],
    totals=dict(vehicle="$46,432.78", options="$425.00", total="$46,857.78"),
    terms=("Pricing per State of Tennessee Statewide Contract (SWC). Quote valid for 30 days unless "
           "otherwise noted. Applicable taxes, title and registration fees are not included."),
)
html = here / "_render.html"
html.write_text(Template((here / "quote.html").read_text()).render(**data))
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium")
    pg = b.new_page(); pg.goto(html.as_uri()); pg.pdf(path=str(here / "sample.pdf"), prefer_css_page_size=True, print_background=True)
    b.close()
html.unlink()
