"""The user's target profile: what to search, which locations to take and how to score job postings.

It lives in ``profile/target.md`` (``PROFILE_PATH``): a TOML header between two ``+++`` lines with
the settings the code uses, and below it the free-text description of the ideal role, which the
LLM reads when scoring. Every section of the header is optional; what is missing falls back to
generic defaults, so ``role`` plus ``[search] keywords`` and a description already work.

    jobradar profile check          # validate the profile and show what the code will use

See ``profile/target.example.md`` for every option.
"""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jobradar import config

FENCE = "+++"


REFERENCE_HEADING = "## Reference posting"  # from here on the profile is a record, not part of the prompts


class ProfileError(RuntimeError):
    """The profile is missing or invalid; the message says how to fix it."""


# ---------------------------------------------------------------- search


@dataclass(frozen=True)
class SearchLocation:
    name: str
    remote: bool = False


@dataclass
class Search:
    keywords: list[str]
    locations: list[SearchLocation]
    max_age_days: int = 45
    sources: dict[str, Any] = field(default_factory=dict)  # per-source overrides (lists or strings)
    niche_sources: list[str] = field(default_factory=list)  # job boards of one field, off unless listed here

    def for_source(self, name: str, default: Any = None) -> Any:
        """The value a source should use: its own override, else ``default``, else ``keywords``."""
        if name in self.sources:
            return self.sources[name]
        return self.keywords if default is None else default


# ---------------------------------------------------------------- location


@dataclass
class Location:
    """Where the candidate lives and which postings they can take from there.

    ``preset = "latam"`` applies the rules tuned for candidates living in Latin America (see
    ``sources/filters.py``); ``"any"`` accepts every location and leaves it to the LLM score.
    """

    preset: str = "any"
    home_name: str = ""  # where the candidate lives, in words (e.g. "Chile")
    home: re.Pattern[str] | None = None  # matches the home city/country in a location or title
    home_country: str = ""  # regex fragment for the home country inside region phrases
    home_codes: frozenset[str] = frozenset()  # ISO codes of the home country
    strict: bool = False  # strict: no single-country remote elsewhere, no US remote


# ---------------------------------------------------------------- scoring


@dataclass
class Dimension:
    key: str
    weight: float
    question: str
    levels: list[str]  # 5 descriptions, from 0 (worst) to 4 (best), for the Jev pre-filter
    uses_cv: bool = False  # the one dimension that looks at the candidate (requirements vs CV)


@dataclass
class Rubric:
    dimensions: list[Dimension]
    identity: dict[str, float]  # dimensions that say "this IS the role": they multiply the score
    identity_floor: float = 0.35
    focus: str = ""  # extra guidance for the LLM: what makes this role THE role and what it is not
    eligibility_question: str = ""
    eligibility_choices: dict[str, str] = field(default_factory=dict)
    eligibility_factors: dict[str, float] = field(default_factory=dict)

    @property
    def weights(self) -> dict[str, float]:
        return {d.key: d.weight for d in self.dimensions}


DEFAULT_DIMENSIONS = [
    Dimension(
        "role_match",
        0.25,
        "Is the CORE of this job the work described in the TARGET (its main responsibilities, not just "
        "a similar title)?",
        [
            "Unrelated work",
            "Mostly different work, some overlap",
            "Partly the target work",
            "Mostly the target work",
            "Clearly and centrally the target role",
        ],
    ),
    Dimension(
        "requirements_fit",
        0.15,
        "Are the job's formal requirements (degrees, years, specific stacks, certifications) accessible to "
        "the candidate (see TARGET and CV)? Score low only when a STRICT requirement cannot be met.",
        [
            "Strictly requires credentials the candidate lacks",
            "Mostly requires such credentials",
            "Some hard requirements, partly flexible",
            "Mostly accessible",
            "Fully accessible",
        ],
        uses_cv=True,
    ),
    Dimension(
        "domain_fit",
        0.15,
        "Is the job in the function, team and context the TARGET asks for, rather than a neighboring one?",
        [
            "A different domain",
            "Mostly another domain",
            "Partly the target domain",
            "Mostly the target domain",
            "Clearly the target domain",
        ],
    ),
    Dimension(
        "skills_focus",
        0.15,
        "Does the job emphasize the skills and tools the TARGET highlights?",
        ["None of them", "Barely", "Some", "A significant part", "Central to the role"],
    ),
    Dimension(
        "company_fit",
        0.10,
        "Does the company match the TARGET's preferences (stage, industry, size, type)?",
        ["Against the preferences", "Mostly not", "Unclear or mixed", "Mostly yes", "Exactly the preferred kind"],
    ),
    Dimension(
        "location_fit",
        0.10,
        "Do the location and work mode match the TARGET's preferences (remote, countries, languages)?",
        ["Incompatible", "Mostly incompatible", "Unclear", "Mostly compatible", "Fully compatible"],
    ),
    Dimension(
        "seniority_fit",
        0.10,
        "Do the seniority and scope match the TARGET (level, individual contributor or manager, ownership)?",
        ["Far off", "Mostly off", "Mixed", "Close", "Exactly the target level and scope"],
    ),
]
DEFAULT_IDENTITY = {"role_match": 0.6, "domain_fit": 0.4}
DEFAULT_ELIGIBILITY = {
    "question": "Can the candidate apply for and do this job from where they live (see TARGET)?",
    "choices": {
        "yes": "The job is where the candidate lives, or remote including that place",
        "no": "Requires living or being authorized to work somewhere else",
        "unclear": "The posting does not say where the job can be done from",
    },
    "factors": {"yes": 1.0, "unclear": 0.9, "no": 0.4},
}


# ---------------------------------------------------------------- companies


@dataclass
class Companies:
    """The target-companies funnel (``jobradar companies``)."""

    ideal: str = ""  # the ideal employer, in words (company classifier)
    country: str = ""  # where the companies are
    country_code: str = ""  # ISO code of that country, for Google searches
    language: str = ""  # language of those searches
    search_location: str = ""  # Apollo search location (default: country)
    fit_guide: str = ""  # how to tell high / medium / low fit, with examples (company classifier)
    good_keywords: list[str] = field(default_factory=list)  # industry/description words of a good fit
    bad_keywords: list[str] = field(default_factory=list)  # ... and of a bad fit (cheap heuristic)
    seed_file: str = ""  # CSV with name,domain,industry of known companies to start from
    # LinkedIn functions of the people to find at each company (default: founders and recruiters)
    contact_functions: list[str] = field(default_factory=lambda: ["Entrepreneurship", "Human Resources"])
    # role buckets for LinkedIn posts of company people (name -> description) and the ones to promote
    post_buckets: dict[str, str] = field(default_factory=dict)
    target_buckets: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- profile


@dataclass
class Profile:
    role: str
    description: str
    search: Search
    location: Location
    scoring: Rubric
    companies: Companies
    outreach_exclude: re.Pattern[str] | None = None  # titles never proposed to contact decision makers
    reference: str = ""  # the posting or description the profile was made from (not sent to the LLM)
    path: Path | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path else None,
            "role": self.role,
            "description_chars": len(self.description),
            "search": {
                "keywords": self.search.keywords,
                "niche_sources": self.search.niche_sources,
                "locations": [loc.name + (" (remote)" if loc.remote else "") for loc in self.search.locations],
                "max_age_days": self.search.max_age_days,
                "source_overrides": sorted(self.search.sources),
            },
            "location": {
                "preset": self.location.preset,
                "home_codes": sorted(self.location.home_codes),
                "strict": self.location.strict,
            },
            "scoring": {"dimensions": self.scoring.weights, "identity": self.scoring.identity},
        }


def path() -> Path:
    value = config.get("PROFILE_PATH") or "profile/target.md"
    p = Path(value)
    return p if p.is_absolute() else config.ROOT / p


def split(text: str) -> tuple[str, str]:
    """(TOML header, description). Without a ``+++`` header the whole text is the description."""
    lines = text.splitlines()
    if lines and lines[0].strip() == FENCE:
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == FENCE:
                return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :]).strip()
        raise ProfileError("the TOML header opened with +++ is never closed")
    return "", text.strip()


def _outreach_exclude(data: dict[str, Any]) -> re.Pattern[str] | None:
    value = data.get("exclude_titles")
    try:
        return re.compile(value, re.I) if value else None
    except re.error as exc:
        raise ProfileError(f"outreach.exclude_titles: invalid regex ({exc})") from None


def _location(data: dict[str, Any]) -> Location:
    preset = data.get("preset", "any")
    if preset not in ("any", "latam"):
        raise ProfileError(f"location.preset must be 'any' or 'latam', not {preset!r}")
    home = data.get("home")
    if preset == "latam" and not (home and data.get("home_country") and data.get("home_codes")):
        raise ProfileError("location.preset = 'latam' needs home, home_country and home_codes")
    return Location(
        preset=preset,
        home_name=str(data.get("home_name", "")),
        home=re.compile(home, re.I) if home else None,
        home_country=data.get("home_country", ""),
        home_codes=frozenset(c.upper() for c in data.get("home_codes") or []),
        strict=bool(data.get("strict", False)),
    )


def _rubric(data: dict[str, Any]) -> Rubric:
    if data.get("dimensions"):
        dims = []
        for i, d in enumerate(data["dimensions"]):
            missing = {"key", "weight", "question", "levels"} - set(d)
            if missing:
                raise ProfileError(f"scoring.dimensions[{i}]: missing {sorted(missing)}")
            if len(d["levels"]) != 5:
                raise ProfileError(f"scoring.dimensions[{i}]: 'levels' needs 5 descriptions (worst to best)")
            dims.append(
                Dimension(d["key"], float(d["weight"]), d["question"], list(d["levels"]), bool(d.get("uses_cv", False)))
            )
    else:
        dims = DEFAULT_DIMENSIONS
    identity = {
        k: float(v) for k, v in (data.get("identity") or ({} if data.get("dimensions") else DEFAULT_IDENTITY)).items()
    }
    keys = {d.key for d in dims}
    if not identity or not set(identity) <= keys or set(identity) == keys:
        raise ProfileError("scoring.identity must name some (not all) of the scoring dimensions")
    elig = {**DEFAULT_ELIGIBILITY, **(data.get("eligibility") or {})}
    if set(elig["factors"]) != {"yes", "no", "unclear"} or set(elig["choices"]) != {"yes", "no", "unclear"}:
        raise ProfileError("scoring.eligibility needs choices and factors for yes, no and unclear")
    return Rubric(
        dimensions=dims,
        identity=identity,
        identity_floor=float(data.get("identity_floor", 0.35)),
        focus=data.get("focus", ""),
        eligibility_question=elig["question"],
        eligibility_choices=dict(elig["choices"]),
        eligibility_factors={k: float(v) for k, v in elig["factors"].items()},
    )


def parse(text: str, source: Path | None = None) -> Profile:
    header, description = split(text)
    try:
        data = tomllib.loads(header)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"the TOML header is invalid: {exc}") from None
    search = data.get("search") or {}
    keywords = [str(k) for k in search.get("keywords") or []]
    locations = [
        SearchLocation(loc["name"], bool(loc.get("remote", False)))
        if isinstance(loc, dict)
        else SearchLocation(str(loc))
        for loc in search.get("locations") or []
    ] or [SearchLocation("Worldwide", True)]
    description, _, reference = description.partition(REFERENCE_HEADING)
    description = description.strip()
    if not description:
        raise ProfileError("the profile has no description of the ideal role below the header")
    if not keywords:
        raise ProfileError("add [search] keywords: job boards are searched for them and titles judged against them")
    return Profile(
        role=str(data.get("role") or "").strip() or "the target role",
        description=description,
        search=Search(
            keywords,
            locations,
            int(search.get("max_age_days", 45)),
            dict(search.get("sources") or {}),
            [str(name) for name in search.get("niche_sources") or []],
        ),
        location=_location(data.get("location") or {}),
        scoring=_rubric(data.get("scoring") or {}),
        companies=_companies(data.get("companies") or {}, str(data.get("role") or "the target role")),
        outreach_exclude=_outreach_exclude(data.get("outreach") or {}),
        reference=reference.strip(),
        path=source,
    )


def _companies(data: dict[str, Any], role: str) -> Companies:
    buckets = {str(k): str(v) for k, v in (data.get("post_buckets") or {}).items()}
    if not buckets:
        buckets = {
            "target": f"an opening for the target role ({role}) or very close to it",
            "other": "any other opening",
        }
    buckets.setdefault("other", "any other opening")
    targets = [str(b) for b in data.get("target_buckets") or [b for b in buckets if b != "other"]]
    if not set(targets) <= set(buckets) - {"other"}:
        raise ProfileError("companies.target_buckets must name post_buckets (other than 'other')")
    return Companies(
        ideal=data.get("ideal", ""),
        country=data.get("country", ""),
        country_code=data.get("country_code", ""),
        language=data.get("language", ""),
        search_location=data.get("search_location", "") or data.get("country", ""),
        fit_guide=data.get("fit_guide", ""),
        good_keywords=[str(k) for k in data.get("good_keywords") or []],
        bad_keywords=[str(k) for k in data.get("bad_keywords") or []],
        seed_file=data.get("seed_file", ""),
        contact_functions=[str(f) for f in data.get("contact_functions") or ["Entrepreneurship", "Human Resources"]],
        post_buckets=buckets,
        target_buckets=targets,
    )


_cache: dict[Path, tuple[float, Profile]] = {}


def load(source: Path | None = None) -> Profile:
    """The profile at ``source`` (default ``PROFILE_PATH``), re-read when the file changes."""
    p = source or path()
    if not p.is_file():
        raise ProfileError(
            f"{p} does not exist. Create it from a job posting you like: `jobradar profile from-url <link>` "
            f"(or `from-text`), or copy profile/target.example.md."
        )
    mtime = p.stat().st_mtime
    cached = _cache.get(p)
    if cached and cached[0] == mtime:
        return cached[1]
    prof = parse(p.read_text(encoding="utf-8"), p)
    _cache[p] = (mtime, prof)
    return prof


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Target profile (profile/target.md): validate it or draft one")
    sub = ap.add_subparsers(dest="cmd", required=True)
    chk = sub.add_parser("check", help="validate the profile and show what the code will use")
    chk.add_argument("--path", type=Path, default=None, help="profile file (default: PROFILE_PATH)")
    url = sub.add_parser("from-url", help="draft a profile from the link of a job posting you like (uses the LLM)")
    url.add_argument("url")
    txt = sub.add_parser(
        "from-text", help="draft a profile from a job posting or a description of the ideal role (uses the LLM)"
    )
    txt.add_argument("file", nargs="?", default="-", help="text file with the posting or description (- = stdin)")
    for p in (url, txt):
        p.add_argument("--notes", default="", help="what you want or not, in your words (optional)")
        p.add_argument(
            "--out", type=Path, default=None, help="file to write (default: the profile, or a draft if there is one)"
        )
    args = ap.parse_args(argv)
    if args.cmd in ("from-url", "from-text"):
        return _draft(args)
    try:
        prof = load(args.path)
    except ProfileError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **prof.summary()}, ensure_ascii=False, indent=2))
    return 0


def _draft(args: argparse.Namespace) -> int:
    import sys

    from jobradar import profile_draft

    try:
        if args.cmd == "from-url":
            posting = profile_draft.fetch(args.url)
        else:
            text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
            posting = profile_draft.Posting(description=text)
        out = profile_draft.write(profile_draft.draft(posting, notes=args.notes), args.out)
    except (profile_draft.DraftError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    prof = parse(out.read_text(encoding="utf-8"), out)
    active = out.resolve() == path().resolve()
    print(
        json.dumps(
            {
                "ok": True,
                "file": str(out),
                "active": active,
                "role": prof.role,
                "keywords": prof.search.keywords,
                "next": "this is now your profile: review the keywords, set [location] to where you live and run "
                "`jobradar profile check`"
                if active
                else "your current profile was not touched: rename this draft to replace it",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
