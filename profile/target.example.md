+++
# jobradar profile. Only `role`, `[search] keywords` and the text below the header are required;
# every other section is optional and falls back to sensible defaults.
# Easiest start: give it a job posting you like or your ideal role in words, and all of this is written
# for you: `jobradar profile from-url <link>` or `jobradar profile from-text <file>`.
# A complete real-world profile: profile/examples/gtm_engineer_latam.md
role = "Data Analyst"

[search]
# what job boards are searched for and what job titles are judged against
keywords = ["data analyst", "analytics engineer", "business intelligence"]
locations = [{name = "Spain", remote = false}, {name = "European Union", remote = true}]  # LinkedIn locations
max_age_days = 45  # older postings are skipped
# niche_sources = ["portal_cargo"]  # job boards of one field: see `jobradar sources list`

# [search.sources]  # optional per-source word lists (default: keywords)
# remote_boards = ["data analyst"]         # Remotive and Himalayas
# getonbrd = ["data analyst"]
# glassdoor_en = ["Data Analyst"]          # remote roles abroad
# glassdoor_local = ["Data Analyst"]       # roles in your country
# hiring_posts_global = ['hiring "data analyst"']  # LinkedIn hiring posts (paid source)

# [outreach]
# exclude_titles = 'intern|internship'  # optional: titles never proposed to contact decision makers

[location]
preset = "any"  # "any": every location passes and the LLM score judges it.
# preset = "latam"  # rules tuned for candidates living in Latin America, for example:
# home_name = "Chile"
# home = '\bchile\b|\bsantiago\b'
# home_country = '\bchile\b'
# home_codes = ["CL"]
# strict = false  # true: no remote jobs limited to another country, no US remote

# [scoring]  # optional: what the LLM scores 0-100 (default: a generic set of dimensions)
# identity = {role_match = 0.6, domain_fit = 0.4}  # the dimensions that say "this IS the role"
# [[scoring.dimensions]]
# key = "role_match"
# weight = 0.25
# question = "Is the CORE of this job the work described in the TARGET?"
# levels = ["Unrelated", "Mostly different", "Partly", "Mostly", "Clearly the target role"]

[companies]
ideal = "Product companies with a data-driven culture; not consulting or staffing firms."
country = "Spain"
# country_code = "ES"  # Google searches of the companies funnel
# language = "es"
# contact_functions = ["Engineering", "Entrepreneurship", "Human Resources"]  # who to find at each company
+++

# Ideal role: Data Analyst

## What the role is
One or two sentences: the work itself, not the title.

## Core responsibilities
- ...

## Typical stack
- ...

## It is NOT
- Roles that look similar but are not what you want.

## The candidate
Background, degree and experience: what makes a job's requirements accessible or not.

## Preferences
Location and remote work, seniority, company type, languages, industries to avoid.
