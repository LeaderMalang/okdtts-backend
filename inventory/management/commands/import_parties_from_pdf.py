from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.conf import settings

import pdfplumber

from setting.models import City, Area
from inventory.models import Party
from hordak.models import Account

# ---------- Settings / Tunables ----------
AR_CODE = getattr(settings, "AR_CODE", "1003")   # Accounts Receivable (parent)
AP_CODE = getattr(settings, "AP_CODE", "2001")   # Accounts Payable (parent)
DEFAULT_CURRENCY = getattr(settings, "DEFAULT_CURRENCY", "PKR")

PHONE_MAX = 20

# Old script heuristics, kept:
DATE_RX  = re.compile(r"(?P<d>\d{2}/\d{2}/\d{4}|\d{2}/\d{4}|/ ?/)")
PHONE_RX = re.compile(r"(?:\+?\d[\d\-\s]{5,})")

# Area header like:  "  1  TOBA TEK SINGH    2  TTS CITY"
AREA_HEADER = re.compile(
    r'^\s*(\d+)\s*([^\d]+?)\s+(\d+)\s*([^\d]+?)\s*$',
    re.IGNORECASE
)

NBSP = "\u00A0"

def normalize_spaces(s: str) -> str:
    return s.replace(NBSP, " ").replace("\u2007", " ").replace("\u202F", " ")

def clean_phone(raw: str | None) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    # keep one leading '+' then digits only
    lead_plus = "+" if raw.startswith("+") else ""
    digits = re.sub(r"\D", "", raw)
    return (lead_plus + digits)[:PHONE_MAX]

def clean_name(text: str) -> str:
    return re.sub(r'^\d+\s*', '', (text or "")).strip()

def _ensure_hordak_party_account(party: Party) -> Account:
    """
    Ensure Party.chart_of_account exists in Hordak.
    Creates a child under AR (customers) or AP (suppliers), sets a stable code.
    """
    if party.chart_of_account:
        return party.chart_of_account

    parent_code = AR_CODE if party.party_type == "customer" else AP_CODE
    try:
        parent = Account.objects.get(code=parent_code)
    except Account.DoesNotExist:
        raise CommandError(
            f"Hordak parent account '{parent_code}' not found. "
            f"Create this parent account (settings AR_CODE / AP_CODE) first."
        )

    acct = Account.objects.create(
        name=f"{party.name} ({party.party_type})",
        parent=parent,
        currency=parent.currency or DEFAULT_CURRENCY,
        code=None,  # set after we know acct.id
    )
    acct.code = f"{parent.code}-{party.party_type[:3].upper()}-{acct.id}"
    acct.save(update_fields=["code"])
    party.chart_of_account = acct
    party.save(update_fields=["chart_of_account"])
    return acct

def parse_customer_line(line: str):
    """
    Parse one customer row line after an area header block.
    Columns are noisy; we peel from the right:
      [ ... ] [date?] [license?] [catg?] [phone?]
    Left remainder => name/address (best-effort).
    """
    raw = " ".join((line or "").split())
    if not raw or raw.startswith(("Client/Area List", "Code Client", "License No", "Dated:", "Page")):
        return None

    # 1) date (rightmost)
    exp = None
    m_date = None
    for m in DATE_RX.finditer(raw):
        m_date = m
    if m_date:
        exp = m_date.group("d").replace(" ", "")
        left = raw[:m_date.start()].strip()
    else:
        left = raw

    # 2) license (token before date if looks alnum with digits)
    license_no = None
    if exp:
        tokens = left.split()
        if tokens:
            cand = tokens[-1]
            if any(ch.isdigit() for ch in cand):
                license_no = cand
                left = " ".join(tokens[:-1])

    # 3) category (short integer before license/date)
    catg = None
    tokens = left.split()
    if tokens:
        cand = tokens[-1]
        if cand.isdigit() and 0 < len(cand) <= 4:
            catg = cand
            left = " ".join(tokens[:-1])

    # 4) phone (longer digit-ish chunk)
    phone = None
    m_phone = None
    for m in PHONE_RX.finditer(left):
        m_phone = m
    if m_phone:
        phone = m_phone.group(0)
        left = (left[:m_phone.start()] + " " + left[m_phone.end():]).strip()

    # Remaining left = name/address mixed.
    name = left
    address = None
    if "," in left:
        name, address = [s.strip() for s in left.split(",", 1)]
    else:
        # heuristic: look for location-ish keyword to split into address
        KW = ("ROAD", "RD", "STREET", "ST", "BAZAR", "NEAR", "CHK", "CANAL", "COLONY", "HOSPITAL", "PARK", "MOHALLA", "CHOWK")
        upp = left.upper()
        cut = -1
        for kw in KW:
            i = upp.rfind(kw)
            if i > cut:
                cut = i
        if cut > 10:
            name = left[:cut].strip(" ,")
            address = left[cut:].strip(" ,")

    # Normalize date -> license_expiry
    license_expiry = None
    if exp and exp not in {"//", "/ /"}:
        try:
            if len(exp) == 10:         # dd/mm/yyyy
                license_expiry = datetime.strptime(exp, "%d/%m/%Y").date()
            elif len(exp) == 7:        # mm/yyyy
                license_expiry = datetime.strptime("01/" + exp, "%d/%m/%Y").date()
        except Exception:
            license_expiry = None

    return {
        "name": clean_name((name or "")[:255]) or None,
        "address": address or "",
        "phone": clean_phone(phone),
        "category": catg or "",
        "license_no": license_no or "",
        "license_expiry": license_expiry,
    }

class Command(BaseCommand):
    help = "Import customers/suppliers from 'Client/Area List' PDF using pdfplumber; auto-creates City/Area/Party and Hordak A/R or A/P accounts."

    def add_arguments(self, parser):
        parser.add_argument("--pdf", required=True, help="Path to the PDF (e.g., all_customer.pdf)")
        parser.add_argument("--party-type", choices=["customer", "supplier", "investor"], default="customer")
        parser.add_argument("--default-city", default=None, help="Fallback city if a header is missing")
        parser.add_argument("--dry-run", action="store_true", help="Parse only; no DB writes")
        parser.add_argument("--limit", type=int, default=None, help="Stop after N rows (debug)")

    @transaction.atomic
    def handle(self, *args, **opts):
        path         = opts["pdf"]
        party_type   = opts["party_type"]
        default_city = opts["default_city"]
        dry_run      = opts["dry_run"]
        limit        = opts["limit"]

        current_city = None
        current_area = None
        created = updated = skipped = 0
        seen = 0

        try:
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    for raw_line in text.splitlines():
                        line = normalize_spaces(raw_line.rstrip())
                        if not line:
                            continue

                        # Area header?
                        mh = AREA_HEADER.match(line)
                        if mh:
                            city = mh.group(2).strip()
                            area = mh.group(4).strip()
                            # override if default_city is forced
                            if default_city:
                                city = default_city
                            if not dry_run:
                                current_city, _ = City.objects.get_or_create(name=city)
                                current_area, _ = Area.objects.get_or_create(name=area, city=current_city)
                            else:
                                current_city, current_area = city, area
                            continue

                        # Parse a customer detail line
                        row = parse_customer_line(line)
                        if not row or not row.get("name"):
                            continue
                        if "<<SoftWave>>" in row.get("name", ""):
                            continue

                        # Ensure we have a context
                        if not current_city:
                            # If no header yet, drop into a default bucket
                            city_name = default_city or "GENERAL"
                            area_name = "GENERAL"
                            if not dry_run:
                                current_city, _ = City.objects.get_or_create(name=city_name)
                                current_area, _ = Area.objects.get_or_create(name=area_name, city=current_city)
                            else:
                                current_city, current_area = city_name, area_name

                        seen += 1
                        if dry_run:
                            self.stdout.write(f"[DRY] {row['name']} -> City={current_city} Area={current_area}")
                        else:
                            # Upsert by (name, party_type, city, area)
                            qs = Party.objects.filter(
                                name=row["name"], party_type=party_type,
                                city=current_city, area=current_area
                            )
                            if qs.exists():
                                p = qs.first()
                                changed = False
                                for fld in ("address", "phone", "category", "license_no"):
                                    new_val = row.get(fld) or ""
                                    if getattr(p, fld) != new_val:
                                        setattr(p, fld, new_val)
                                        changed = True
                                if row.get("license_expiry") and p.license_expiry != row["license_expiry"]:
                                    p.license_expiry = row["license_expiry"]
                                    changed = True
                                if changed:
                                    p.save()
                                    updated += 1
                                else:
                                    skipped += 1
                            else:
                                p = Party.objects.create(
                                    name=row["name"],
                                    party_type=party_type,
                                    address=row.get("address", ""),
                                    phone=row.get("phone", "")[:PHONE_MAX],
                                    category=row.get("category", ""),
                                    license_no=row.get("license_no", ""),
                                    license_expiry=row.get("license_expiry", None),
                                    credit_limit=Decimal("0"),
                                    current_balance=Decimal("0"),
                                    city=current_city,
                                    area=current_area,
                                )
                                created += 1

                            # Ensure Hordak account (for both new and existing)
                            #_ensure_hordak_party_account(p)

                        if limit and (created + updated + skipped) >= limit:
                            raise StopIteration

        except StopIteration:
            pass
        except FileNotFoundError as e:
            raise CommandError(str(e))
        except Exception as e:
            # Bubble up with context
            raise CommandError(f"Import error: {e}")

        if dry_run:
            transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS(
            f"Done. seen={seen}, created={created}, updated={updated}, skipped={skipped}{' (dry-run)' if dry_run else ''}"
        ))
