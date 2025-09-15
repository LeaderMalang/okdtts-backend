import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
HEADERS = {"User-Agent": "pharma-erp/1.0 (support@yourdomain.com)"}

def reverse_geocode(lat: float, lon: float) -> dict:
    try:
        r = requests.get(
            NOMINATIM_URL,
            params={
                "format": "jsonv2",
                "lat": lat,
                "lon": lon,
                "addressdetails": 1,
                "zoom": 18,
                "accept-language": "en"
            },
            headers=HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        addr = data.get("address", {})
        return {
            "ok": True,
            "display": data.get("display_name"),
            "city": addr.get("district").replace(' District',''),
            "area": addr.get("town"),
            "road": addr.get("road"),
            "postcode": addr.get("postcode"),
            "state": addr.get("state"),
            "country": addr.get("country"),
            "raw": addr,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
