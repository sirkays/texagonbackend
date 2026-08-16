import os
from typing import Dict, Any

APP_BRAND = os.environ.get("APP_BRAND", "techxagon").lower().strip()

BRAND_CONFIGS: Dict[str, Dict[str, Any]] = {
    "techxagon": {
        "id": "techxagon",
        "name": "Techxagon",
        "full_name": "Techxagon Academy",
        "short_name": "Techxagon",
        "tagline": "Readying the Future",
        "default_from_email": "Techxagon Team <noreply@techxagonacademy.com>",
        "domain_email": "noreply@techxagonacademy.com",
        "logo_url": "https://learn.techxagonacademy.com/logo.png",
        "frontend_origin": "https://learn.techxagonacademy.com",
        "support_email": "info@techxagonacademy.com",
        "cors_origins": [
            "https://learn.techxagonacademy.com",
            "https://techxagonacademy.com",
        ],
        "csrf_origins": [
            "https://learn.techxagonacademy.com",
            "https://techxagonacademy.com",
        ],
    },
    "nimet": {
        "id": "nimet",
        "name": "NiMet",
        "full_name": "Nigerian Meteorological Agency",
        "short_name": "NiMet",
        "tagline": "Authoritative Weather & Climate Services",
        "default_from_email": "NiMet Learning Portal <noreply@nimet.gov.ng>",
        "domain_email": "noreply@nimet.gov.ng",
        "logo_url": "https://nimet.gov.ng/assets/img/logo.png",
        "frontend_origin": "https://learn.nimet.gov.ng",
        "support_email": "info@nimet.gov.ng",
        "cors_origins": [
            "https://learn.nimet.gov.ng",
            "https://nimet.gov.ng",
            "https://nimet-web.onrender.com",
        ],
        "csrf_origins": [
            "https://learn.nimet.gov.ng",
            "https://nimet.gov.ng",
            "https://nimet-web.onrender.com",
        ],
    },
}

def get_active_brand() -> str:
    brand = os.environ.get("APP_BRAND", "techxagon").lower().strip()
    return "nimet" if brand == "nimet" else "techxagon"

def get_brand_config() -> Dict[str, Any]:
    brand = get_active_brand()
    return BRAND_CONFIGS.get(brand, BRAND_CONFIGS["techxagon"])

brand_config = get_brand_config()
