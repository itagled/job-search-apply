"""Groups Apollo's ~189 raw industries into ~40 manageable groups.

Keyword-rule mapping (ordered: first match wins), applied to lowercase text
without accents. Covers both Apollo's taxonomy and the Spanish hints from the
curated seed. Populates the ``industry_group`` column.
"""

from __future__ import annotations

from jobradar.text import strip_accents

# Ordered rules (group, [substrings]). The FIRST match wins, so
# specific verticals (fintech, edtech, ...) come before their generic parents.
RULES: list[tuple[str, list[str]]] = [
    (
        "Government and Public Sector",
        [
            "government",
            "public policy",
            "public safety",
            "law enforcement",
            "political",
            "international affairs",
            "public administration",
        ],
    ),
    ("Defense and Aerospace", ["defense", "military", "aviation & aerospace", "aerospace"]),
    ("Cybersecurity", ["network security"]),
    (
        "Fintech and Finance",
        [
            "fintech",
            "financial",
            "finance",
            "banking",
            "investment",
            "capital market",
            "venture capital",
            "private equity",
            "pagos",
            "remesas",
            "cobros",
            "scoring",
            "financiamiento",
            "credito",
            "inversiones",
        ],
    ),
    ("Insurance and Insurtech", ["insurance", "insurtech", "seguros"]),
    ("Edtech", ["edtech", "e-learning"]),
    (
        "Education",
        [
            "education",
            "educacion",
            "colegio",
            "primary/secondary",
            "higher education",
            "training & coaching",
            "professional training",
            "academ",
            "universit",
        ],
    ),
    ("Healthtech, Pharma and Biotech", ["healthtech", "pharmaceutical", "biotech", "medical devices"]),
    (
        "Health and Wellness",
        [
            "health",
            "salud",
            "wellness",
            "fitness",
            "hospital",
            "medical",
            "mental health",
            "veterinary",
            "alternative medicine",
        ],
    ),
    ("Foodtech and Restaurants", ["foodtech", "restaurant", "restaurante"]),
    ("Food and Beverages", ["food", "beverage", "wine", "spirits", "tobacco", "aliment"]),
    ("Proptech and Real Estate", ["proptech", "real estate", "inmobiliaria"]),
    (
        "Construction and Materials",
        ["construction", "construccion", "building materials", "glass, ceramics", "concrete"],
    ),
    (
        "Engineering",
        ["civil engineering", "mechanical or industrial engineering", "industrial engineering", "nanotechnology"],
    ),
    ("Design and Architecture", ["architecture", "graphic design", "design", "photography"]),
    (
        "Industry and Manufacturing",
        [
            "machinery",
            "manufacturing",
            "industrial automation",
            "packaging",
            "plastics",
            "printing",
            "paper & forest",
            "chemicals",
            "business supplies",
            "textiles",
        ],
    ),
    ("Hardware and Electronics", ["computer hardware", "consumer electronics", "semiconductor"]),
    ("Telecommunications", ["telecommunication", "telecom", "wireless"]),
    (
        "IT and Software",
        [
            "information technology",
            "software",
            "saas",
            "internet",
            "information services",
            "program development",
            "computer networking",
            "artificial",
            " ai",
            "erp",
            "tech ",
        ],
    ),
    (
        "Marketing and Advertising",
        ["marketing", "advertising", "public relations", "market research", "communications"],
    ),
    (
        "Media and Entertainment",
        [
            "media",
            "entertainment",
            "publishing",
            "broadcast",
            "motion pictures",
            "film",
            "music",
            "performing arts",
            "animation",
            "computer games",
            "gaming",
            "fine art",
            "writing & editing",
            "translation",
        ],
    ),
    (
        "Tourism and Hospitality",
        ["travel", "tourism", "hospitality", "leisure", "recreational", "sports", "sporting goods", "pasajes"],
    ),
    ("E-commerce", ["e-commerce", "ecommerce"]),
    ("Fashion, Textiles and Cosmetics", ["apparel", "fashion", "textile", "luxury goods", "cosmetics", "jewelry"]),
    (
        "Retail and Commerce",
        ["retail", "wholesale", "import & export", "supermarket", "consumer goods", "import", "export", "furniture"],
    ),
    (
        "Logistics and Transportation",
        [
            "logistics",
            "logistica",
            "logtech",
            "transport",
            "trucking",
            "railroad",
            "freight",
            "warehousing",
            "maritime",
            "delivery",
            "courier",
            "airlines",
            "aviation",
        ],
    ),
    ("Automotive", ["automotive", "automotriz"]),
    ("HR and Recruiting", ["human resources", "staffing", "recruiting", "hr tech", "rrhh"]),
    ("Legal", ["legal", "law practice", "law firm", "legaltech", "dispute resolution", "law "]),
    ("Accounting", ["accounting", "contabilidad"]),
    (
        "Consulting and Professional Services",
        ["consulting", "consultor", "outsourcing", "executive office", "research"],
    ),
    (
        "Nonprofits and Social Organizations",
        [
            "nonprofit",
            "civic",
            "social organization",
            "individual & family",
            "religious",
            "fund-raising",
            "philanthropy",
            "think tanks",
            "international trade",
            "museums",
            "libraries",
            "international development",
        ],
    ),
    ("Energy", ["oil & energy", "energy", "energia", "utilities", "solar"]),
    ("Environment and Sustainability", ["environmental", "renewables"]),
    ("Agriculture", ["farming", "agriculture", "ranching", "agtech", "agri"]),
    ("Mining and Metals", ["mining", "metals", "mineria"]),
    ("Security and Investigations", ["security & investigations", "security"]),
    ("Facilities and General Services", ["facilities", "events services", "package"]),
    ("Consumer Services and Pets", ["consumer services", "pet care", "packaging & containers"]),
]

# Default group name when no rule matches.
DEFAULT_GROUP = "Other / Unclassified"


def map_industry_group(raw: str | None) -> str | None:
    """Return the group (~40) for a raw industry, or None if it's None/empty."""
    text = strip_accents(raw)
    if not text:
        return None
    # Short exact cases that a generic substring doesn't capture well.
    if text in ("ai", "inteligencia artificial"):
        return "IT and Software"
    for group, keywords in RULES:
        if any(kw in text for kw in keywords):
            return group
    return DEFAULT_GROUP
