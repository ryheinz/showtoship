"""
site_configs.py — Per-site scraping configs (CSS selectors or API-based)
"""

CONFIGS: dict[str, dict] = {
    "smm-hamburg.com": {
        "name": "SMM Hamburg",
        "type": "api",
        "api_config": {
            "endpoint": "https://live.messebackend.aws.corussoft.de/webservice/search",
            "method": "POST",
            "content_type": "application/x-www-form-urlencoded",
            "base_params": {
                "os": "web",
                "appUrl": "https://www.smm-hamburg.com",
                "clientVersion": "1.15.0",
                "topic": "2026_smm",
                "apiVersion": "52",
                "browserLang": "en-US",
                "filterlist": "entity_orga,,cur_curated",
                "order": "lexic",
                "timezoneOffset": "0",
                "lang": "en",
            },
            "page_size": 200,
            "total_count": 0,
            "response_type": "xml",
            "entity_path": ".//entities/organization",
            "field_map": {
                "company_name": {"attr": "name"},
                "email": {"attr": "email"},
                "website": {"attr": "web"},
                "country": {"attr": "country"},
                "country_code": {"attr": "countryCode"},
                "city": {"attr": "city"},
                "hall": {"path": "stands/stand", "attr": "hallNr"},
                "stand": {"path": "stands/stand", "attr": "standNr"},
                "description": {"path": "description/teaser", "text": True},
            },
            "init_page": "https://www.smm-hamburg.com/exhibit-visit/exhibitor-directory",
        },
    },
}

def get_config_for_domain(url: str) -> dict | None:
    for domain, config in CONFIGS.items():
        if domain in url:
            return config
    return None
