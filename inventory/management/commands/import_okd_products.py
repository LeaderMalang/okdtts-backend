# yourapp/management/commands/import_okd_products.py
import re
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

try:
    import pdfplumber  # pip install pdfplumber
except ImportError:
    pdfplumber = None

from inventory.models import Product # adjust import path
from setting.models import Company, Group, Distributor


ROW_START = re.compile(r"^\s*(\d{2,5})\s+")  # product code at line start
NUMBER = re.compile(r"(-?\d+\.\d{2})")       # capture 12.34 style numbers


def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_line(line: str):
    """
    Parse one product line from the PDF text.
    Returns dict with:
      code, name, packing (best-effort), rate, retail, d_rate
    We tolerate extra columns (e.g., tax %, discount %).
    Strategy:
      - code = leading integer
      - name+packing = text up to first price number; we then try to split
      - prices = all 12.34 numbers on the line; rate = prices[0], d_rate = prices[-1]
      - retail = second price if present and it makes sense
    """
    m = ROW_START.match(line)
    if not m:
        return None
    code = m.group(1)

    # Everything after the code:
    rest = line[m.end():].rstrip()

    # First price index to cut name/packing
    first_price_match = NUMBER.search(rest)
    if not first_price_match:
        return None

    name_pack = clean_spaces(rest[:first_price_match.start()])
    prices = [Decimal(p) for p in NUMBER.findall(rest)]

    # Heuristic price mapping:
    rate = prices[0] if len(prices) >= 1 else None
    # Many pages show columns: Rate, (maybe tax), (maybe discount%), D.Rate as last
    d_rate = prices[-1] if prices else None
    retail = None
    if len(prices) >= 2:
        # If there are >=3 numeric columns, very often second *isn't* retail; but if many lines
        # include a clear "Retail" before discount columns, keep this best-effort rule:
        retail = prices[1]

    # Split name/packing (packing often at the end like "1s", "10s", "100s", "120ml", "450ml", etc.)
    pack_m = re.search(r"(\b\d+(?:s|ml|gm|g|kg|KG|ML|G|L)\b)$", name_pack)
    if pack_m:
        packing = pack_m.group(1)
        name = clean_spaces(name_pack[: pack_m.start()])
    else:
        packing = ""
        name = name_pack

    # Final sanity: if retail > rate, keep it; else leave None
    if retail is not None and rate is not None and retail <= rate:
        # retail column sometimes not present/accurate; drop it to avoid bad data
        retail = None

    return {
        "code": code,
        "name": name,
        "packing": packing,
        "rate": rate,
        "retail": retail,
        "d_rate": d_rate,
    }


class Command(BaseCommand):
    help = "Import OK Distributors product list PDF into Product model."

    def add_arguments(self, parser):
        parser.add_argument("pdf_path", type=str, help="Path to the product-list PDF")
        parser.add_argument("--company", required=True, help="Company name to assign")
        parser.add_argument("--group", required=True, help="Group name to assign")
        parser.add_argument("--distributor", default="OK DISTRIBUTORS", help="Distributor name")
        parser.add_argument(
            "--price-column",
            choices=["rate", "retail", "d_rate"],
            default="d_rate",
            help="Which column maps to Product.trade_price (rate alias). Default: d_rate",
        )
        parser.add_argument("--dry-run", action="store_true", help="Parse only; do not write DB")
        parser.add_argument("--verbose-lines", action="store_true", help="Print each parsed line")

    def handle(self, *args, **opts):
        if pdfplumber is None:
            raise CommandError("pdfplumber is required. Install with: pip install pdfplumber")

        pdf_path = Path(opts["pdf_path"])
        if not pdf_path.exists():
            raise CommandError(f"File not found: {pdf_path}")

        price_col = opts["price_column"]

        # Resolve related FKs
        company, _ = Company.objects.get_or_create(name=opts["company"])
        group, _ = Group.objects.get_or_create(name=opts["group"])
        distributor, _ = Distributor.objects.get_or_create(name=opts["distributor"])

        parsed = []

        # Extract text
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                for raw_line in text.splitlines():
                    line = raw_line.strip()
                    row = parse_line(line)
                    if row:
                        row['code']=row['name'].split(' ')[0]
                        row['name']=row['name'].split(' ')[1]
                        parsed.append(row)
                        print(row)
                        if opts["verbose_lines"]:
                            self.stdout.write(f"[OK] {row['code']}: {row['name']} ({row['packing']})  "
                                              f"rate={row['rate']}  retail={row['retail']}  d_rate={row['d_rate']}")

        if not parsed:
            raise CommandError("No product rows parsed – check the PDF content or parser patterns.")

        self.stdout.write(f"Parsed {len(parsed)} rows.")

        if opts["dry_run"]:
            self.stdout.write("Dry-run complete. No database changes made.")
            return

        created = 0
        updated = 0

        @transaction.atomic
        def _write():
            nonlocal created, updated
            for row in parsed:
                trade_price = row.get(price_col) or row.get("rate") or row.get("d_rate")
                if trade_price is None:
                    # skip if we can't determine price
                    continue

                defaults = {
                    "company": company,
                    "group": group,
                    "distributor": distributor,
                    "trade_price": trade_price,
                    "packing": row.get("packing", ""),
                    # if retail not parsed or unrealistic, copy trade_price
                    "retail_price": row.get("retail") or trade_price,
                    "sales_tax_ratio": Decimal("0.00"),
                    "fed_tax_ratio": Decimal("0.00"),
                    "disable_sale_purchase": False,
                }

                # Prefer to match by barcode=code when present; else by name
                product = None
                if Product.objects.filter(barcode=row["code"]).exists():
                    product = Product.objects.get(barcode=row["code"])
                elif Product.objects.filter(name=row["name"]).exists():
                    product = Product.objects.get(name=row["name"])

                if product:
                    for k, v in defaults.items():
                        setattr(product, k, v)
                    if not product.barcode:
                        product.barcode = row["code"]
                    product.name = row["name"]  # keep name current
                    product.save()
                    updated += 1
                else:
                    Product.objects.create(
                        name=row["name"],
                        barcode=row["code"],
                        **defaults,
                    )
                    created += 1

        _write()

        self.stdout.write(self.style.SUCCESS(
            f"Done. Created: {created}, Updated: {updated} (price column='{price_col}')."
        ))
