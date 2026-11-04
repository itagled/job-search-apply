"""Job posting filters: is the title the role the profile looks for? can the candidate take it?

- ``title_in_family``: Jev's answer for the title, against the role of the profile (see ``titles``).
- ``eligible``: the location rules of the profile (``[location]``). Preset ``latam`` is for
  candidates living in Latin America: their country, LATAM or global remote; US remote without
  an explicit residency requirement and remote limited to one other LATAM country, unless
  ``strict``. Preset ``any`` accepts every location and leaves it to the LLM score.
- ``accept``: both filters on a posting, plus the age limit of the profile.

The geography below was hand-tuned, posting by posting, in the September and October 2026
sweeps; hence the long, explicit regular expressions.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING

from jobradar import config, profile
from jobradar.sources import titles

if TYPE_CHECKING:
    from jobradar.sources.base import Posting

# --- Location: the "latam" preset ---------------------------------------------------------

US_STATES = "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"

RE_LOC_GLOBAL = re.compile(
    r"\blatam\b|\blat\.?\s?am\b|latin\s*america|latinoam[eé]rica|am[eé]rica\s*latina|south\s*america|sudam[eé]rica|"
    r"\bamericas\b|\bam[eé]ricas\b|anywhere|worldwide|^\s*global\s*$|global\s*[-(]?\s*remote|remote\s*[-(]?\s*global|"
    r"any\s*(location|country)|all\s*locations|work\s*from\s*anywhere|\bwfa\b|remote\s*\(?\s*(any|world)",
    re.I,
)

LATAM_COUNTRIES = [
    "argentina",
    "bolivia",
    "brazil",
    "brasil",
    "colombia",
    "costa rica",
    "ecuador",
    "el salvador",
    "guatemala",
    "honduras",
    "mexico",
    "méxico",
    "nicaragua",
    "panama",
    "panamá",
    "paraguay",
    "peru",
    "perú",
    "uruguay",
    "venezuela",
    "dominican republic",
    "república dominicana",
    "cdmx",
    "mexico city",
    "ciudad de méxico",
    "são paulo",
    "sao paulo",
    "buenos aires",
    "bogota",
    "bogotá",
    "medellin",
    "medellín",
    "lima",
    "montevideo",
    "guadalajara",
    "monterrey",
    "rio de janeiro",
    "curitiba",
    "florianópolis",
    "florianopolis",
    "belo horizonte",
    "quito",
    "asunción",
    "asuncion",
    "córdoba",
    "cordoba",
    "cali",
    "barranquilla",
    "san josé",
    "san jose, costa rica",
]

LATAM_NAME2ISO = {
    "argentina": "AR",
    "buenos aires": "AR",
    "córdoba": "AR",
    "cordoba": "AR",
    "bolivia": "BO",
    "brazil": "BR",
    "brasil": "BR",
    "são paulo": "BR",
    "sao paulo": "BR",
    "rio de janeiro": "BR",
    "curitiba": "BR",
    "florianópolis": "BR",
    "florianopolis": "BR",
    "belo horizonte": "BR",
    "colombia": "CO",
    "bogota": "CO",
    "bogotá": "CO",
    "medellin": "CO",
    "medellín": "CO",
    "cali": "CO",
    "barranquilla": "CO",
    "costa rica": "CR",
    "san josé": "CR",
    "san jose, costa rica": "CR",
    "ecuador": "EC",
    "quito": "EC",
    "el salvador": "SV",
    "guatemala": "GT",
    "honduras": "HN",
    "mexico": "MX",
    "méxico": "MX",
    "cdmx": "MX",
    "mexico city": "MX",
    "ciudad de méxico": "MX",
    "guadalajara": "MX",
    "monterrey": "MX",
    "nicaragua": "NI",
    "panama": "PA",
    "panamá": "PA",
    "paraguay": "PY",
    "asunción": "PY",
    "asuncion": "PY",
    "peru": "PE",
    "perú": "PE",
    "lima": "PE",
    "uruguay": "UY",
    "montevideo": "UY",
    "venezuela": "VE",
    "dominican republic": "DO",
    "república dominicana": "DO",
}

RE_LATAM_C = re.compile(r"\b(" + "|".join(re.escape(c) for c in LATAM_COUNTRIES) + r")\b", re.I)

RE_FOREIGN = re.compile(
    r"\bUSA?\b|U\.S\.A?\.?|united\s*states|\bUS[-\s]?(remote|based|only)\b|north\s*america|\bNAMER\b|\bNORAM\b|canada|toronto|vancouver|montreal|ottawa|calgary|"
    r"new\s*york|\bNYC\b|san\s*francisco|\bSF\b|bay\s*area|los\s*angeles|seattle|austin|boston|chicago|denver|atlanta|miami|dallas|houston|"
    r"philadelphia|washington|san\s*diego|san\s*mateo|palo\s*alto|mountain\s*view|menlo\s*park|sunnyvale|san\s*jose,?\s*ca|salt\s*lake|provo|lehi|"
    r"portland|phoenix|nashville|raleigh|pittsburgh|minneapolis|detroit|columbus|charlotte|tampa|orlando|las\s*vegas|brooklyn|"
    r"california|texas|florida|colorado|massachusetts|illinois|georgia|virginia|utah|oregon|arizona|ohio|michigan|minnesota|"
    r"\bEMEA\b|\bAPAC\b|\bAPJ\b|europe|\bEU\b|\bEEA\b|\bUK\b|united\s*kingdom|england|scotland|ireland|dublin|london|manchester|"
    r"germany|deutschland|berlin|munich|münchen|hamburg|cologne|köln|frankfurt|düsseldorf|stuttgart|austria|vienna|wien|switzerland|zurich|zürich|geneva|"
    r"france|paris|lyon|spain|españa|madrid|barcelona|valencia|portugal|lisbon|lisboa|porto|italy|milan|milano|rome|turin|torino|"
    r"netherlands|amsterdam|rotterdam|belgium|brussels|luxembourg|denmark|copenhagen|sweden|stockholm|norway|oslo|finland|helsinki|"
    r"poland|warsaw|krakow|kraków|czech|prague|hungary|budapest|romania|bucharest|bulgaria|sofia|serbia|belgrade|croatia|greece|athens|"
    r"cyprus|malta|estonia|tallinn|latvia|lithuania|ukraine|kyiv|turkey|türkiye|istanbul|israel|tel\s*aviv|"
    r"india|bangalore|bengaluru|hyderabad|pune|gurgaon|gurugram|noida|mumbai|delhi|chennai|"
    r"singapore|australia|sydney|melbourne|new\s*zealand|auckland|japan|tokyo|korea|seoul|china|shanghai|beijing|hong\s*kong|taiwan|"
    r"philippines|manila|indonesia|jakarta|vietnam|malaysia|thailand|bangkok|pakistan|"
    r"south\s*africa|cape\s*town|johannesburg|nigeria|lagos|kenya|nairobi|egypt|cairo|morocco|"
    r"dubai|\bUAE\b|united\s*arab|saudi|riyadh|qatar|doha|"
    r",\s*(" + US_STATES + r")\b",
    re.I,
)

RE_DESC_FOREIGN_ONLY = re.compile(
    r"(must|need\s*to|required\s*to|should|will\s*need\s*to)\s*(be\s*)?(currently\s*)?(reside|residing|live|living|located|based|be\s*based|be\s*located|work)\s*"
    r"[^.\n]{0,50}(united\s*states|\bu\.?s\.?a?\b|us-based|canada|\buk\b|united\s*kingdom|europe|\beu\b|germany|india|australia|israel|philippines)|"
    r"authori[sz](ed|ation)\s*to\s*work\s*(in|for)[^.\n]{0,30}(united\s*states|\bu\.?s\.?\b|canada|\buk\b|united\s*kingdom|\beu\b|europe)|"
    r"\b(us|u\.s\.)[\s-]*based\s*(candidates|applicants|only|employees|role)|"
    r"only\s*(open\s*to|accepting|considering|hiring)[^.\n]{0,50}(united\s*states|\bu\.?s\.?\b|us\s*residents|canada|\buk\b|europe|\beu\b)|"
    r"within\s*the\s*(united\s*states|u\.s\.|us|contiguous|continental\s*u)|"
    r"remote\s*[\(\-–,]\s*(us|u\.s\.|usa|united\s*states|uk|canada|eu|europe)\b|"
    r"\b(us|u\.s\.|usa)\s*only\b|"
    r"right\s*to\s*work\s*in\s*the\s*(uk|eu|united\s*kingdom|european)|"
    r"(eligible|eligibility)\s*to\s*work\s*in\s*the\s*(us|u\.s\.|united\s*states|uk|eu|european\s*union|canada)|"
    r"(not|unable\s*to|cannot|can't|don't|do\s*not)\s*(provide\s*|offer\s*)?sponsor",
    re.I,
)

LATAM_ISO = {
    "AR",
    "BO",
    "BR",
    "CL",
    "CO",
    "CR",
    "EC",
    "SV",
    "GT",
    "HN",
    "MX",
    "NI",
    "PA",
    "PY",
    "PE",
    "UY",
    "VE",
    "DO",
    "CU",
    "PR",
}

OTHER_COUNTRIES = [
    "afghanistan",
    "albania",
    "algeria",
    "andorra",
    "angola",
    "armenia",
    "azerbaijan",
    "bahrain",
    "bangladesh",
    "belarus",
    "benin",
    "bhutan",
    "bosnia",
    "botswana",
    "brunei",
    "burkina",
    "burundi",
    "cambodia",
    "cameroon",
    "chad",
    "comoros",
    "congo",
    "djibouti",
    "eritrea",
    "eswatini",
    "ethiopia",
    "fiji",
    "gabon",
    "gambia",
    "georgia",
    "ghana",
    "guinea",
    "iceland",
    "iran",
    "iraq",
    "ivory coast",
    "côte d'ivoire",
    "jamaica",
    "jordan",
    "kazakhstan",
    "kosovo",
    "kuwait",
    "kyrgyzstan",
    "laos",
    "lebanon",
    "lesotho",
    "liberia",
    "libya",
    "liechtenstein",
    "macedonia",
    "north macedonia",
    "madagascar",
    "malawi",
    "maldives",
    "mali",
    "mauritania",
    "mauritius",
    "moldova",
    "monaco",
    "mongolia",
    "montenegro",
    "mozambique",
    "myanmar",
    "namibia",
    "nepal",
    "niger",
    "oman",
    "palestine",
    "papua",
    "rwanda",
    "senegal",
    "sierra leone",
    "slovakia",
    "slovenia",
    "somalia",
    "sri lanka",
    "sudan",
    "syria",
    "tajikistan",
    "tanzania",
    "togo",
    "trinidad",
    "tunisia",
    "turkmenistan",
    "uganda",
    "uzbekistan",
    "yemen",
    "zambia",
    "zimbabwe",
    "kuala lumpur",
    "cis region",
    "barbados",
    "bahamas",
    "belize",
    "guyana",
    "suriname",
    "haiti",
]

RE_OTHER_C = re.compile(r"\b(" + "|".join(re.escape(c) for c in OTHER_COUNTRIES) + r")\b", re.I)

RE_ANYWHERE_FOREIGN = re.compile(
    r"anywhere\s*in\s*(the\s*)?(uk|united\s*kingdom|europe|eu|emea|us|usa|u\.s\.|united\s*states|canada|india|apac|"
    r"germany|spain|portugal|poland|north\s*america|asia|africa)",
    re.I,
)


@lru_cache(maxsize=8)
def _region_patterns(home_country: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """Title and description phrases that open a job to Latin America or to the home country."""
    home = f"|{home_country}" if home_country else ""
    title_loc = re.compile(
        r"\blatam\b|latin\s*america|latinoam[eé]rica|south\s*america"
        + home
        + r"|anywhere|worldwide|remote\s*\(?\s*(global|any)",
        re.I,
    )
    hires = re.compile(
        r"(open\s*to|hire|hiring|based\s*in|located\s*in|reside\s*in|residing\s*in|living\s*in|candidates?\s*(in|from|across|located)|"
        r"talent\s*(in|from|across)|remote\s*(in|from|across|within)|work\s*from|anywhere\s*in|applicants?\s*(in|from))"
        r"[^.\n]{0,45}(latam|latin\s*america|south\s*america" + home + r"|the\s*americas)|"
        r"(latam|latin\s*america|south\s*america)[\s-]*(based|only|residents?|candidates?)|"
        r"(work|live|living|based|located|remote(ly)?)\s*[^.\n]{0,20}anywhere\s*in\s*the\s*world|"
        r"(us|u\.s\.)\s*or\s*latam|latam\s*or\s*(us|u\.s\.)|americas\s*time\s*zones?|time\s*zones?\s*(in|across)\s*the\s*americas",
        re.I,
    )
    return title_loc, hires


RE_ONSITE = re.compile(r"hybrid|h[ií]brido|on[\s-]?site|in[\s-]?office|presencial|IN_OFFICE|ON_SITE", re.I)

RE_REMOTE = re.compile(r"remote|remoto|remota|REMOTE|telecommute|work\s*from\s*home", re.I)

RE_US_LOC = re.compile(
    r"united\s*states|\busa\b|\bu\.s\.a?\b|\bUS\b|north\s*america|new\s*york|\bNYC\b|san\s*francisco|\bSF\b|bay\s*area|"
    r"los\s*angeles|seattle|austin|boston|chicago|denver|atlanta|miami|dallas|houston|philadelphia|washington|san\s*diego|"
    r"palo\s*alto|salt\s*lake|portland|phoenix|nashville|raleigh|pittsburgh|minneapolis|california|texas|florida|colorado|"
    r"massachusetts|illinois|georgia|virginia|utah|oregon|arizona|ohio|michigan|minnesota|,\s*(" + US_STATES + r")\b",
    re.I,
)

RE_US_EXPLICIT_ONLY = re.compile(
    r"(must|need\s*to|required\s*to|will\s*need\s*to)\s*(be\s*)?(currently\s*)?(reside|residing|live|living|located|based|be\s*based|"
    r"be\s*located)\s*[^.\n]{0,40}(united\s*states|\bu\.?s\.?a?\b|us-based)|"
    r"authori[sz](ed|ation)\s*to\s*work\s*(in|for)[^.\n]{0,30}(united\s*states|\bu\.?s\.?\b)|"
    r"(eligible|eligibility)\s*to\s*work\s*in\s*the\s*(us|u\.s\.|united\s*states)|"
    r"\b(us|u\.s\.|usa)\s*only\b|only\s*(open\s*to|accepting|considering|hiring)[^.\n]{0,40}(united\s*states|\bu\.?s\.?\b|us\s*residents)|"
    r"\b(us|u\.s\.)\s*(citizens?|citizenship|residents?\s*only|persons?)\b|security\s*clearance|\bITAR\b|\bW-?2\b|"
    r"(not|unable\s*to|cannot|can't|don't|do\s*not)\s*(provide\s*|offer\s*)?sponsor",
    re.I,
)

RE_DESC_REMOTE = re.compile(
    r"\b(fully|100%|completely)\s*remote|remote[\s-]first|remote[\s-](role|position|job|team|company)|"
    r"work\s*(fully\s*)?remote(ly)?|distributed\s*team",
    re.I,
)


def _latam(title: str, loc: str, desc: str, countries: list, where: profile.Location) -> tuple[bool, str]:
    """The ``latam`` preset: can someone living in ``where.home`` take this job?"""
    loc = RE_ANYWHERE_FOREIGN.sub(" ", loc or "")
    title = title or ""
    desc = desc or ""
    strict = where.strict
    title_loc, hires = _region_patterns(where.home_country)
    codes = {str(c).strip().upper() for c in (countries or []) if c and len(str(c).strip()) == 2}
    if where.home.search(loc) or codes & where.home_codes or where.home.search(title):
        return True, "home"
    if RE_ONSITE.search(title) or (RE_ONSITE.search(loc) and not RE_REMOTE.search(loc)):
        return False, "onsite_or_hybrid_outside_home"
    g = RE_LOC_GLOBAL.search(loc)
    if g:
        return True, "loc_latam_or_global:" + g.group(0)
    if title_loc.search(title):
        return True, "title_latam_or_global"
    hire_latam = hires.search(desc)
    foreign_only = RE_DESC_FOREIGN_ONLY.search(desc)
    latam_c = {LATAM_NAME2ISO.get(m.group(0).lower(), m.group(0).lower()) for m in RE_LATAM_C.finditer(loc)} | (
        codes & LATAM_ISO
    )
    if latam_c:
        if len(latam_c) >= 2 or hire_latam:
            return True, "latam_countries:" + ",".join(sorted(latam_c))
        if not strict:
            return True, "single_other_latam_country_relaxed:" + ",".join(latam_c)
        return False, "single_other_latam_country:" + ",".join(latam_c)
    foreign = RE_FOREIGN.search(loc) or RE_OTHER_C.search(loc)
    foreign_codes = codes - LATAM_ISO
    if foreign or foreign_codes:
        if hire_latam and not foreign_only:
            return True, "ambiguous_foreign_loc_but_desc_hires_latam:" + hire_latam.group(0)[:50]
        us_loc = RE_US_LOC.search(loc) or (foreign_codes and foreign_codes <= {"US"})
        other_foreign = (
            RE_OTHER_C.search(loc) or (foreign_codes - {"US"}) or (foreign and not RE_US_LOC.search(foreign.group(0)))
        )
        is_remote = RE_REMOTE.search(loc) or RE_REMOTE.search(title) or RE_DESC_REMOTE.search(desc)
        if not strict and us_loc and not other_foreign and is_remote and not RE_US_EXPLICIT_ONLY.search(desc):
            return True, "us_remote_relaxed:" + (us_loc.group(0) if hasattr(us_loc, "group") else "US")
        return False, "foreign_location:" + (foreign.group(0) if foreign else ",".join(sorted(foreign_codes)))
    if foreign_only and not hire_latam:
        return False, "desc_requires_foreign:" + foreign_only.group(0)[:60]
    if hire_latam:
        return True, "desc_hires_latam:" + hire_latam.group(0)[:50]
    return True, "ambiguous_remote_or_unspecified"


def title_in_family(title: str | None) -> tuple[bool, str]:
    """Is the title the role the profile looks for? Returns (ok, reason)."""
    if not (title or "").strip():
        return False, "no_title"
    if titles.score(title) < (config.get_float("TITLE_MIN") or 0.0):
        return False, "title_not_target"
    return True, "title_match"


def eligible(
    title: str | None, location: str | None, description: str | None, countries: list | None = None
) -> tuple[bool, str]:
    """Can the candidate take the job from where they live? Returns (ok, reason)."""
    where = profile.load().location
    if where.preset == "any":
        return True, "any_location"
    return _latam(title or "", location or "", description or "", countries or [], where)


def home_matches(text: str | None) -> bool:
    """Does ``text`` name the candidate's home (city or country)? Always False with preset ``any``."""
    home = profile.load().location.home
    return bool(home and home.search(text or ""))


def out_of_reach_on_site(text: str | None) -> bool:
    """On-site or hybrid work in ``text`` is ruled out: away from home (with preset ``any``, the LLM judges it)."""
    return profile.load().location.preset != "any" and not home_matches(text)


def hires_in_region(text: str | None) -> re.Match[str] | None:
    """A phrase saying the job is open to Latin America or to the candidate's country."""
    where = profile.load().location
    return _region_patterns(where.home_country)[1].search(text or "") if where.preset == "latam" else None


def accept(posting: Posting, *, now: datetime | None = None) -> tuple[bool, str]:
    """Title, eligibility and age of a posting. Returns (ok, reason)."""
    ok, why = title_in_family(posting.title)
    if not ok:
        return False, "title:" + why
    max_age = profile.load().search.max_age_days
    limit = (now or datetime.now(UTC)) - timedelta(days=max_age)
    if posting.posted_at is not None and posting.posted_at < limit:
        return False, f"old>{max_age}d"
    ok, why = eligible(posting.title, posting.location, posting.description, posting.countries)
    return (True, why) if ok else (False, "elig:" + why)
