"""
site_configs.py — Per-site CSS selector configs for popular tradeshow websites
-------------------------------------------------------------------------------
Import this in scraper.py to use site-specific extraction instead of the generic schema.

Usage in scraper.py:
    from site_configs import get_config_for_url
    strategy = get_config_for_url(url) or default_strategy
"""

from crawl4ai.extraction_strategy import JsonCssExtractionStrategy

# ─── Site-specific schemas ────────────────────────────────────────────────────

CONFIGS: dict[str, dict] = {

    # 10times.com — large tradeshow directory
    "10times.com": {
        "name": "10Times",
        "schema": {
            "name": "EventList",
            "baseSelector": ".event-box, .event-card, .list-item",
            "fields": [
                {"name": "name",       "selector": ".event-name, h3, .title",    "type": "text"},
                {"name": "date_start", "selector": ".event-date, .date",         "type": "text"},
                {"name": "location",   "selector": ".event-venue, .city",        "type": "text"},
                {"name": "country",    "selector": ".country",                   "type": "text"},
                {"name": "industry",   "selector": ".category, .industry",       "type": "text"},
                {"name": "website",    "selector": "a.event-name",               "type": "attribute", "attribute": "href"},
            ]
        }
    },

    # expodatabase.com
    "expodatabase.com": {
        "name": "ExpoDatabase",
        "schema": {
            "name": "ExpoList",
            "baseSelector": "tr.tradeshow-row, .tradeshow-item",
            "fields": [
                {"name": "name",       "selector": "td.name a, .show-name",      "type": "text"},
                {"name": "date_start", "selector": "td.date, .show-date",        "type": "text"},
                {"name": "location",   "selector": "td.city, .show-city",        "type": "text"},
                {"name": "country",    "selector": "td.country",                 "type": "text"},
                {"name": "industry",   "selector": "td.industry",                "type": "text"},
                {"name": "website",    "selector": "td.name a",                  "type": "attribute", "attribute": "href"},
            ]
        }
    },

    # tradefairdates.com
    "tradefairdates.com": {
        "name": "TradeFairDates",
        "schema": {
            "name": "FairList",
            "baseSelector": ".fair-item, tr.fair-row",
            "fields": [
                {"name": "name",       "selector": ".fair-title, h3",            "type": "text"},
                {"name": "date_start", "selector": ".fair-date-from",            "type": "text"},
                {"name": "date_end",   "selector": ".fair-date-to",              "type": "text"},
                {"name": "location",   "selector": ".fair-location",             "type": "text"},
                {"name": "country",    "selector": ".fair-country",              "type": "text"},
                {"name": "industry",   "selector": ".fair-sector",               "type": "text"},
                {"name": "website",    "selector": "a.fair-link",                "type": "attribute", "attribute": "href"},
            ]
        }
    },

    # messekalender.eu
    "messekalender.eu": {
        "name": "MesseKalender",
        "schema": {
            "name": "MesseList",
            "baseSelector": ".messe-item, tr.messe",
            "fields": [
                {"name": "name",       "selector": ".messe-name, h2",            "type": "text"},
                {"name": "date_start", "selector": ".datum-von",                 "type": "text"},
                {"name": "date_end",   "selector": ".datum-bis",                 "type": "text"},
                {"name": "location",   "selector": ".ort, .veranstaltungsort",   "type": "text"},
                {"name": "country",    "selector": ".land",                      "type": "text"},
                {"name": "industry",   "selector": ".branche",                   "type": "text"},
            ]
        }
    },

    # biztradeshows.com
    "biztradeshows.com": {
        "name": "BizTradeShows",
        "schema": {
            "name": "ShowList",
            "baseSelector": ".show-listing, .event-item",
            "fields": [
                {"name": "name",       "selector": "h2 a, .show-title",          "type": "text"},
                {"name": "date_start", "selector": ".show-dates, .dates",        "type": "text"},
                {"name": "location",   "selector": ".show-location, .location",  "type": "text"},
                {"name": "industry",   "selector": ".show-industry, .industry",  "type": "text"},
                {"name": "website",    "selector": "h2 a",                       "type": "attribute", "attribute": "href"},
                {"name": "description","selector": ".show-desc, .description",   "type": "text"},
            ]
        }
    },
}


def get_config_for_url(url: str):
    """Return a JsonCssExtractionStrategy if we have a config for this domain."""
    for domain, config in CONFIGS.items():
        if domain in url:
            return JsonCssExtractionStrategy(config["schema"], verbose=True)
    return None


def list_supported_sites() -> list[str]:
    return list(CONFIGS.keys())
