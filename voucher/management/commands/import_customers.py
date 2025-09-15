import re
import json
from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.timezone import make_aware

import pdfplumber

from setting.models import City, Area
from inventory.models import Party  # adjust if your Party app label is different
from voucher.models import ChartOfAccount
import random
AREA_HEADER = re.compile(
    r'^\s*(\d+)\s+([A-Za-z][A-Za-z\s\.\-&]+?)\s{2,}(\d+)\s+([A-Za-z][A-Za-z\s\.\-&]+)\s*$'
)

DATE_RX = re.compile(r"(?P<d>\d{2}/\d{2}/\d{4}|\d{2}/\d{4}|/ ?/)")
DIGITS_RX = re.compile(r"\d")
PHONE_RX = re.compile(r"(?:\+?\d[\d\-\s]{5,})")
CATG_RX = re.compile(r"\b\d{1,4}\b")
PHONE_MAX = 20

def clean_phone(raw: str | None) -> str:
    if not raw:
        return ""
    # keep digits and plus sign at start
    raw = raw.strip()
    m = re.match(r'^\+?', raw)  # keep one leading +
    digits = re.sub(r'\D', '', raw)
    phone = (m.group(0) if m else '') + digits
    # clip hard to DB limit
    return phone[:PHONE_MAX]
NBSP = "\u00A0"

def normalize_spaces(s: str) -> str:
    # Replace non-breaking spaces and collapse runs of spaces
    s = s.replace(NBSP, " ").replace("\u2007", " ").replace("\u202F", " ")
    # Keep long runs (2+) because they separate the two columns
    return s

def clean_name(text: str) -> str:
    # Remove leading numbers and spaces
    return re.sub(r'^\d+\s*', '', text).strip()

def split_city_area(label: str):
    """
    Heuristic split of 'TOBA TEK SINGH TTS CITY' -> ('TOBA TEK SINGH','TTS CITY')
    Rules:
      1) Prefer known multi-word city prefixes (common in PK): try 3 words, then 2.
      2) If label contains ' CITY' or ' ROAD' etc., take everything after the city guess as area.
      3) Fallback: first word = city, rest = area.
    You can extend CITY_HINTS to suit your data.
    """
    label = " ".join(label.split())
    parts = label.split()

    CITY_HINTS = {
        # add more if needed
        "TOBA TEK SINGH",
        "KAMALIA",
        "JHANG",
        "SANDHILIANWALI",
        "SHORKOT",
        "PIRMAHAL",
        "GOJRA",
    }

    # try 3-word city, then 2-word, else 1-word
    for n in (3, 2, 1):
        if len(parts) >= n:
            ctry = " ".join(parts[:n])
            if ctry in CITY_HINTS or n == 1:
                city = ctry
                area = " ".join(parts[n:]).strip() or ctry  # if nothing left, duplicate as area
                return city, area

    # fallback
    return label, label


def parse_customer_line(line: str):
    """
    Parse one customer row line after an area header block.
    Target columns (best-effort from your PDF):
      Client Name | Proprietor | Address | Phone | Catg. | License No. | Exp.Date
    The text flow is inconsistent, so we peel from the right:
      ... [license?] [date?] [catg?] [phone?]
    Whatever remains at left => name/proprietor/address; we attempt to split proprietor if we see two
    consecutive uppercase-ish tokens at start, but if not sure, keep everything as 'name'.
    """
    raw = " ".join(line.split())
    if not raw or raw.startswith(("Client/Area List", "Code Client", "License No", "Dated:", "Page")):
        return None

    # 1) pull date (rightmost match)
    lic_exp = None
    exp = None
    m_date = None
    for m in DATE_RX.finditer(raw):
        m_date = m  # keep last
    if m_date:
        exp = m_date.group("d").replace(" ", "")
        left = raw[:m_date.start()].strip()
    else:
        left = raw

    # 2) pull license (token before date if alnum-ish)
    license_no = None
    if exp:
        tokens = left.split()
        if tokens:
            cand = tokens[-1]
            if any(ch.isdigit() for ch in cand):
                license_no = cand
                left = " ".join(tokens[:-1])

    # 3) pull category (a short integer before license/date)
    catg = None
    tokens = left.split()
    if tokens:
        cand = tokens[-1]
        if cand.isdigit() and 0 < len(cand) <= 4:
            catg = cand
            left = " ".join(tokens[:-1])

    # 4) pull phone (longer digit-ish chunk)
    phone = None
    m_phone = None
    for m in PHONE_RX.finditer(left):
        m_phone = m
    if m_phone:
        phone = m_phone.group(0)
        left = (left[:m_phone.start()] + " " + left[m_phone.end():]).strip()

    # Remaining 'left' = name/proprietor/address mixed. Keep it in 'name' & 'address' best-effort.
    name = left
    proprietor = None
    address = None

    # Tiny heuristic: if '  ' or comma separates, try split near middle
    if "," in left:
        name, address = [s.strip() for s in left.split(",", 1)]
    else:
        # split by last two words into address if they look like a location keyword
        KW = ("ROAD", "RD", "STREET", "ST", "BAZAR", "NEAR", "CHK", "CANAL", "COLONY", "HOSPITAL", "PARK")
        upp = left.upper()
        cut = -1
        for kw in KW:
            i = upp.rfind(kw)
            if i > cut:
                cut = i
        if cut > 10:
            name = left[:cut].strip(" ,")
            address = left[cut:].strip(" ,")

    # Normalize date
    license_expiry = None
    if exp and exp not in {"//", "/ /"}:
        # handle dd/mm/yyyy or mm/yyyy
        try:
            if len(exp) == 10:
                license_expiry = datetime.strptime(exp, "%d/%m/%Y").date()
            elif len(exp) == 7:
                license_expiry = datetime.strptime("01/" + exp, "%d/%m/%Y").date()
        except Exception:
            license_expiry = None

    return {
        "name":clean_name(name[:255]) if name else None,
        "proprietor": proprietor,
        "address": address,
        "phone": phone,
        "category": catg,
        "license_no": license_no,
        "license_expiry": license_expiry,
    }

# remove numeric prefix from each word
def _strip_num_prefix_per_word(s: str) -> str:
    return " ".join(re.sub(r"^\d+", "", w) for w in s.split())
class Command(BaseCommand):
    help = "Import customers from 'Client/Area List' PDF; auto-creates City/Area/Party."

    def add_arguments(self, parser):
        parser.add_argument("--pdf", required=True, help="Path to the customer PDF")
        parser.add_argument("--default-city", default=None, help="Force a default city if header parsing fails")
        parser.add_argument("--party-type", default="customer", help="Party type to set (default: customer)")
        parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
        parser.add_argument("--limit", type=int, default=None, help="Stop after N customers (debug)")

    @transaction.atomic
    def handle(self, *args, **opts):
        path = opts["pdf"]
        default_city = opts["default_city"]
        party_type = opts["party_type"]
        dry_run = opts["dry_run"]
        limit = opts["limit"]

        current_city = None
        current_area = None
        created = 0
        updated = 0
        skipped = 0

        try:
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    i=0
                    for raw_line in text.splitlines():
                        line = raw_line.rstrip()

                        # Detect area header
                        # print(line)
                        AREA_HEADER = re.compile(
                            r'^\s*(\d+)\s*([^\d]+?)\s+(\d+)\s*([^\d]+?)\s*$',
                            re.IGNORECASE
                        )
                        mh = AREA_HEADER.match(normalize_spaces(line))
                        
                        if mh:
                            city_id, city, area_id, area = int(mh.group(1)), mh.group(2).strip(), int(mh.group(3)), mh.group(4).strip()
                        # if mh:
                            # label = mh.group("label").strip()
                            # city, area = split_city_area(label)
                            # print(f"Detected area header: City='{_strip_num_prefix_per_word(city)}' Area='{_strip_num_prefix_per_word(area)}'")
                            if default_city:
                                city = default_city
                            current_city, _ = City.objects.get_or_create(name=city) if not dry_run else (city, True)
                            current_area, _ = Area.objects.get_or_create(
                                name=area, city=current_city if not dry_run and isinstance(current_city, City) else None
                            ) if not dry_run else (area, True)
                            continue

                        # Parse customer row
                        row = parse_customer_line(line)
                        if not row:
                            continue
                        if not row.get("name"):
                            skipped += 1
                            continue
                        if "<<SoftWave>>" in row.get("name"):
                            skipped += 1
                            continue

                        # Require area context; if missing, try default city or bucket into "GENERAL"
                        if not current_city:
                            if default_city:
                                current_city, _ = City.objects.get_or_create(name=default_city) if not dry_run else (default_city, True)
                            else:
                                # put everything into a generic bucket per page until a header appears
                                gc_name = "GENERAL"
                                ga_name = "GENERAL"
                                current_city, _ = City.objects.get_or_create(name=gc_name) if not dry_run else (gc_name, True)
                                current_area, _ = Area.objects.get_or_create(
                                    name=ga_name, city=current_city if not dry_run and isinstance(current_city, City) else None
                                ) if not dry_run else (ga_name, True)

                        if dry_run:
                             
                            self.stdout.write(f"[DRY] {row['name']}  -> City={current_city} Area={current_area}")
                        else:
                            
                            #create ChartOfAccount
                            i=i+1
                            i=random.randint(0,99999)+i
                            code="CUSACC"+str(i).zfill(4)
                            # print(code)
                            account=ChartOfAccount.objects.create(name=row['name'],account_type_id=1,code=code,parent_account_id=3)
                            # print(account.id)
                            # Upsert Party by (name, city, area)
                            party, created_flag = Party.objects.get_or_create(
                                name=row["name"],
                                chart_of_account=account,
                                defaults={
                                    "party_type": party_type,
                                    "address": row.get("address") or "",
                                    "phone": clean_phone(row.get("phone")) or "",
                                    "proprietor": row.get("proprietor") or "",
                                    "license_no": row.get("license_no") or "",
                                    "license_expiry": row.get("license_expiry"),
                                    "category": row.get("category") or "",
                                    "city": current_city,
                                    "area": current_area,
                                },
                            )
                            if not created_flag:
                                # update basic fields if blank / changed
                                changed = False
                                for fld in ("address", "phone", "proprietor", "license_no", "category"):
                                    val = row.get(fld) or ""
                                    if getattr(party, fld) != val:
                                        setattr(party, fld, val)
                                        party.chart_of_account=account
                                        changed = True
                                if row.get("license_expiry") and party.license_expiry != row["license_expiry"]:
                                    party.license_expiry = row["license_expiry"]
                                    changed = True
                                if party.city_id != getattr(current_city, "id", party.city_id):
                                    party.city = current_city
                                    changed = True
                                if party.area_id != getattr(current_area, "id", party.area_id):
                                    party.area = current_area
                                    changed = True
                                if changed:
                                    party.save()
                                    updated += 1
                                else:
                                    skipped += 1
                            else:
                                created += 1

                        if limit and (created + updated + skipped) >= limit:
                            raise StopIteration

        except StopIteration:
            pass
        except FileNotFoundError as e:
            raise CommandError(str(e))
        except Exception as e:
            raise

        self.stdout.write(self.style.SUCCESS(
            f"Done. created={created}, updated={updated}, skipped={skipped}"
        ))
