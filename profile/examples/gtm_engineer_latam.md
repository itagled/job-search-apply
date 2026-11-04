+++
# jobradar profile: what to search, which locations to take and how to score postings.
# Example: GTM Engineer roles for a candidate living in Chile (also used by the tests).
# Every section is optional; see profile/target.example.md. The text below the header is the ideal role.
role = "GTM Engineer"

[search]
keywords = [
  "GTM Engineer", "Go To Market", "Growth Engineer", "Growth", "RevOps", "Revenue Operations",
  "Revenue Engineer", "Sales Operations", "Marketing Operations", "Marketing Automation",
  "Operaciones Comerciales", "HubSpot", "CRM"
]
locations = [{name = "Chile", remote = false}, {name = "Latin America", remote = true}]
max_age_days = 45

niche_sources = ["portal_cargo", "web_clay_community"]  # job boards of this field

[search.sources]  # per-source word lists
remote_boards = [
  "gtm engineer", "go to market", "go-to-market engineer", "gtm systems", "gtm operations", "gtm automation",
  "revops", "revenue operations", "revenue systems", "revenue engineer", "sales operations", "sales ops",
  "sales systems", "sales automation", "marketing operations", "marketing ops", "marketing automation",
  "marketing technology", "martech", "growth engineer", "growth operations", "technical growth",
  "growth hacker", "business operations", "bizops", "strategy and operations", "commercial operations",
  "commercial excellence", "deal desk", "partner operations", "customer success operations", "crm manager",
  "crm specialist", "hubspot", "salesforce", "salesforce admin", "business systems", "automation specialist",
  "ai automation", "workflow automation", "n8n", "zapier", "clay", "outbound", "lifecycle operations",
  "demand generation operations", "lead generation", "operaciones comerciales", "automatización"
]
getonbrd = [
  "revops", "revenue operations", "growth", "gtm", "go to market", "sales operations",
  "operaciones comerciales", "hubspot", "crm", "salesforce", "automatizacion", "automation",
  "marketing operations", "marketing automation", "inteligencia comercial", "excelencia comercial",
  "business operations", "growth engineer", "revenue", "n8n", "zapier", "clay", "outbound", "sales ops",
  "bizops", "ia comercial", "ai automation", "martech"
]
glassdoor_en = [
  "GTM Engineer", "GTM Operations", "Revenue Operations", "Sales Operations", "Marketing Operations",
  "Marketing Automation", "Business Operations", "Growth Engineer", "HubSpot", "Revenue Engineer",
  "Lead Generation", "Growth Marketing Engineer", "Demand Generation Operations", "Head of Growth",
  "Outbound Automation"
]
glassdoor_local = [
  "Revenue Operations", "Sales Operations", "Marketing Automation", "Growth", "CRM", "GTM",
  "Marketing Operations", "Lead Generation"
]
wellfound_roles_ops = [
  "sales-operations", "marketing-operations", "operations-manager", "growth-marketer", "growth-hacker",
  "sales", "marketing", "business-development-manager"
]
wellfound_roles_eng = [
  "software-engineer", "full-stack-engineer", "backend-engineer"
]
wellfound_title_ops = "gtm, go-to-market, go to market, revops, revenue operations, revenue ops, revenue engineer, sales operations, sales ops, marketing operations, marketing ops, business operations, bizops, growth engineer, growth operations, growth ops, marketing automation, sales automation, automation, hubspot, crm, salesforce, gtm systems, revenue systems, lead gen, lead generation, demand gen, outbound"
wellfound_title_eng = "gtm, go-to-market, growth, revenue, revops, automation, hubspot, crm, salesforce, integrations, internal tools"
hiring_posts_global = [
  "hiring \"GTM engineer\"", "hiring \"GTM engineering\"", "hiring \"go-to-market engineer\"",
  "hiring \"growth engineer\"", "hiring \"revenue operations\" remote", "hiring \"RevOps\"",
  "hiring \"sales operations\" remote", "hiring \"marketing operations\" remote",
  "hiring \"GTM systems\" OR \"GTM operations\"", "hiring \"revenue engineer\"",
  "hiring \"HubSpot\" admin OR architect OR specialist", "hiring \"marketing automation\" remote",
  "hiring \"lead generation\" remote", "hiring \"growth marketing engineer\"",
  "hiring \"head of growth\" remote", "hiring \"demand generation\" remote", "hiring \"Clay\" \"GTM\" remote",
  "hiring \"outbound\" \"automation\"", "hiring \"AI automation\" \"GTM\" OR \"sales\" OR \"marketing\"",
  "hiring \"bizops\" OR \"business operations\" remote", "hiring \"growth operations\"",
  "hiring \"GTM engineer\" \"remote\""
]
hiring_posts_local = [
  "hiring \"LATAM\" \"revenue operations\"", "hiring \"LATAM\" \"GTM\"", "hiring \"Latin America\" \"RevOps\"",
  "hiring \"LATAM\" \"growth\" remote", "hiring \"LATAM\" \"HubSpot\"",
  "hiring \"LATAM\" \"marketing operations\" OR \"marketing ops\"",
  "hiring \"LATAM\" \"sales operations\" OR \"sales ops\"", "hiring \"LATAM\" \"lead generation\"",
  "hiring \"Chile\" \"GTM\" OR \"RevOps\" OR \"growth\"",
  "\"buscamos\" \"Chile\" \"growth\" OR \"RevOps\" OR \"GTM\"",
  "\"buscamos\" \"RevOps\" OR \"revenue operations\"", "\"estamos contratando\" \"growth\"",
  "\"buscamos\" \"growth\" \"remoto\"",
  "\"buscamos\" \"sales ops\" OR \"sales operations\" OR \"operaciones comerciales\"", "\"buscamos\" \"GTM\"",
  "\"buscamos\" \"HubSpot\" OR \"CRM\" \"remoto\"",
  "\"buscamos\" \"automatización\" \"comercial\" OR \"marketing\"",
  "\"buscamos\" \"lead generation\" OR \"generación de leads\"",
  "\"buscamos\" \"head of growth\" OR \"growth lead\"",
  "\"buscamos\" \"marketing ops\" OR \"marketing operations\" OR \"operaciones de marketing\"",
  "\"vaga\" \"RevOps\" OR \"revenue operations\" OR \"GTM\"", "\"vaga\" \"growth\" \"remoto\""
]
linkedin_title_include = ["gtm", "go to market", "go-to-market", "growth", "revops", "revenue operations", "revenue ops", "revenue engineer", "revenue automation", "sales operations", "sales ops", "salesops", "sales automation", "marketing operations", "marketing ops", "marketing automation", "martech", "demand generation", "operaciones comerciales", "operaciones ventas", "operaciones marketing", "automatización marketing", "automatización comercial", "automatización ventas", "business operations", "bizops", "crm", "hubspot", "salesforce"]
hiring_posts_relevant = "Relevant roles (is_relevant=true): GTM engineering, growth (engineer, marketing, head/lead of growth, growth ops),\nRevOps / revenue operations, sales ops, marketing ops, marketing automation, lead generation / demand generation\noperations, outbound automation, business operations / bizops, AI automation for sales or marketing, CRM / HubSpot /\nSalesforce admin, Clay. NOT relevant: pure sales reps (SDR, BDR, AE, closers, appointment setters), pure software\nengineering unrelated to GTM, design, finance, HR, customer support."
hiring_posts_eligible = "true if someone living in Chile could take it (based in Chile, remote LATAM / Latin America, remote\nglobal / anywhere, remote in the Americas, remote US-company without explicit US residency requirement); false if it is\non-site or hybrid outside Chile, or remote restricted to another country/region (US only, Europe only, India...);\nnull if unknown."

[outreach]
# titles never proposed to contact decision makers (jobradar decision-makers), even with a high score
exclude_titles = 'forward[- ]deployed|solutions? engineer|sales engineer|pre-?sales|account executive|\bsdr\b|\bbdr\b|business development rep|sales development rep|implementation (consultant|specialist|engineer|manager)|ai implementation|intern\b|internship|pasant|practicante|trainee|full[- ]stack|backend|frontend|data scientist|machine learning engineer|ml engineer|devops|site reliability'

[location]
preset = "latam"
home_name = "Chile"
home = '\bchile\b|\bsantiago\b(?!\s*de\s*compostela)'
home_country = '\bchile\b'
home_codes = ["CL"]
strict = false

[scoring]
identity = {gtm_build_focus = 0.6, gtm_domain_context = 0.4}
identity_floor = 0.35
focus = "Key distinction of GTM Engineering: BUILDING systems that connect product and market (lead-to-revenue, enrichment, scoring, routing, automated outbound, CRM engineering, AI agents). It is NOT: SDR/sales, admin of a single tool (e.g. Salesforce admin), RevOps that only optimizes processes without building, product software engineering without GTM context (full-stack of an app, product backend, plain Angular/Node/.NET), or research data science."

[[scoring.dimensions]]
key = "gtm_build_focus"
weight = 0.22
question = "Is the CORE of the role BUILDING GTM/growth/automation systems (lead-to-revenue: capture, enrich, score and route leads, CRM engineering, automated outbound, agentic workflows), and NOT operating tools, selling, or product software engineering?"
levels = [
  "Not at all: sales, support, product engineering or unrelated work",
  "Mostly operating tools or processes, little building",
  "Mixed: some building of GTM systems among other duties", "Mostly building GTM/revenue systems",
  "Clearly and centrally a GTM/growth systems builder role"
]

[[scoring.dimensions]]
key = "accessibility_fit"
weight = 0.16
question = "Are the job's requirements ACCESSIBLE for the candidate, judging by the CV (a builder from the commercial side, technically capable in Python/LLMs/APIs/SQL)? Score LOW if the posting STRICTLY requires a degree, years in a role or senior mastery of a stack that the CV does not show (for example years as a software engineer on Angular/.NET/Java enterprise). Score HIGH if it values builders, product, commercial sense and applied AI. Do NOT penalize technical work itself: lower it only when a requirement is a credential or kind of experience the CV does not provide, and weigh how strict it is (mandatory vs 'or equivalent experience')."
levels = [
  "Strictly requires credentials the CV does not show", "Mostly requires such credentials",
  "Some hard requirements, partly flexible", "Mostly accessible",
  "Fully accessible, values builders/commercial/AI"
]
uses_cv = true

[[scoring.dimensions]]
key = "gtm_domain_context"
weight = 0.14
question = "Is it clearly in a go-to-market context (sales/marketing/growth/RevOps that builds), and NOT product engineering, data science/ML research, or non-technical operations?"
levels = [
  "Not go-to-market at all", "Mostly outside go-to-market", "Partly go-to-market", "Mostly go-to-market",
  "Clearly go-to-market"
]

[[scoring.dimensions]]
key = "ai_llm_automation"
weight = 0.14
question = "Does the vacancy involve LLMs/AI agents and/or GTM pipeline automation (signal-based outbound, enrichment, routing, personalization)?"
levels = [
  "None", "Barely mentioned", "Some", "Significant part", "Central to the role"
]

[[scoring.dimensions]]
key = "technical_handson"
weight = 0.1
question = "Does it require hands-on technical building: Python/SQL/APIs/webhooks/integrations/ETL?"
levels = [
  "No technical building", "Light no-code at most", "Some technical work",
  "Substantial hands-on technical work", "Core is hands-on technical building"
]

[[scoring.dimensions]]
key = "company_stage_fit"
weight = 0.08
question = "Is the company AI-first / B2B SaaS / a startup / high leverage, versus a traditional enterprise, staffing/agency, or a non-tech industry (mining, etc.)?"
levels = [
  "Non-tech industry or staffing agency", "Traditional enterprise", "Unclear or mixed",
  "Tech company / B2B SaaS", "AI-first or high-growth B2B startup"
]

[[scoring.dimensions]]
key = "location_modality"
weight = 0.08
question = "Is it remote or remote-friendly / in Chile or LATAM / compatible with English? On-site outside Chile without a remote option scores low."
levels = [
  "On-site outside Chile, no remote", "Hybrid outside Chile", "Unclear", "Remote with some restrictions",
  "Remote LATAM/global or in Chile"
]

[[scoring.dimensions]]
key = "seniority_scope_fit"
weight = 0.08
question = "Is it an individual contributor with end-to-end ownership / building scope / a 0-to-1 mindset, versus pure people management or a very junior admin role?"
levels = [
  "Pure people management or very junior admin", "Mostly management or narrow admin", "Mixed",
  "IC with meaningful ownership", "IC owning end-to-end builds, 0-to-1"
]

[scoring.eligibility]
question = "Can the candidate apply for and do this job LIVING IN CHILE? 'yes' if the posting is remote for LATAM / the Americas / global / 'anywhere' including Chile, or if the job is in Chile (on-site or hybrid in Chile works). 'no' if: (1) the location is outside Chile and the posting does NOT say the role is remote (assume on-site or hybrid); (2) it is hybrid or on-site outside Chile; (3) the location is in Europe, Asia, Africa, Oceania or Canada, unless it says remote global/LATAM/anywhere; or (4) it explicitly requires living in or being authorized to work in another country ('must reside in', 'authorized/eligible to work in the US', 'US citizens', 'US only', 'no sponsorship', security clearance). 'unclear' ONLY if: the posting has no location outside Chile and does not say where to live; or it is REMOTE in the US without explicitly requiring residency or work authorization there (many companies hire in LATAM as contractors or through an EOR); or it is REMOTE limited to ONE Latin American country other than Chile (e.g. 'Remote - Brazil'). Base it on what the posting and its location say."
choices = {yes = "Remote for LATAM / Americas / global / anywhere including Chile, or the job is in Chile", no = "Located outside Chile and NOT stated as remote (assume on-site/hybrid); or hybrid/on-site outside Chile; or located in Europe, Asia, Africa, Oceania or Canada unless remote global/LATAM/anywhere; or EXPLICITLY requires living in or being authorized to work in another country (must reside in, authorized/eligible to work in the US, US citizens, US only, no visa sponsorship)", unclear = "Only if: no location outside Chile and no statement of where to live; or REMOTE in the US without explicitly requiring US residency/work authorization; or REMOTE limited to one Latin American country other than Chile"}
factors = {yes = 1.0, unclear = 0.9, no = 0.4}

[companies]
ideal = "AI-first startups, B2B SaaS and high-growth tech companies whose go-to-market can be automated and scaled (B2C or B2B SMB: SaaS, fintech, e-commerce, marketplaces, delivery). Not niche or enterprise channels (mining, heavy industry, government, bespoke consulting)."
country = "Chile"
country_code = "CL"  # Google searches
language = "es"
search_location = "Santiago, Chile"
contact_functions = ["Sales", "Business Development", "Product Management", "Entrepreneurship", "Human Resources"]
fit_guide = "- high: a mass-market, standardizable product sold to MANY customers in a repeatable way (B2C, B2B to small businesses, SaaS, fintech, e-commerce, marketplace). Benefits from automated outreach at scale.\n- low: niche or consultative selling, few large customers, bespoke enterprise, or specialized industries (mining, heavy industry, government, NGOs). Mass outreach adds little.\n- medium: intermediate or ambiguous cases.\n\nEXAMPLES\n- \"A technology company with AI-powered software for restaurants in LatAm, 2018\" -> startup, high, b2b_smb.\n- \"A consumer fintech that simplifies investing, 2016\" -> startup, high, b2c.\n- \"A state-owned copper miner, 1976\" -> NOT a startup, low, b2b_enterprise.\n- \"A school\" -> NOT a startup, low, other."
good_keywords = ["saas", "software", "fintech", "e-commerce", "ecommerce", "comercio electronico", "marketplace", "consumer", "consumo", "app", "aplicacion", "plataforma", "delivery", "logistica", "insurtech", "seguros", "proptech", "inmobiliaria", "healthtech", "salud digital", "edtech", "educacion", "hr tech", "recursos humanos", "rrhh", "payments", "pagos", "prestamos", "lending", "credito", "creditos", "banking", "banca digital", "suscripcion", "subscription", "crm", "martech", "adtech", "foodtech", "restaurantes", "travel", "turismo", "gaming", "streaming", "telecom", "movilidad", "transporte", "pymes", "small business", "freelancers", "b2b saas", "erp", "contabilidad", "beneficios", "bienestar", "energia solar", "paneles solares", "solar residencial", "agtech"]
bad_keywords = ["mineria", "minera", "minero", "mining", "petroleo", "oil", "gas natural", "industrial pesado", "manufactura pesada", "maquinaria", "heavy machinery", "defensa", "aeroespacial", "gobierno", "sector publico", "utilities", "infraestructura", "construccion pesada", "forestal", "acuicola", "salmon", "pesca industrial", "cemento", "siderurgia", "quimica industrial", "consultora", "consulting", "integrador", "system integrator", "distribucion mayorista", "wholesale"]
target_buckets = ["gtm_engineer", "growth", "sdr_bdr", "sales_automation", "revops"]

[companies.post_buckets]
gtm_engineer = "GTM Engineer, Go-To-Market Engineer, growth/marketing/sales engineer focused on GTM automation and tooling."
growth = "Growth (growth lead, growth manager, head of growth, growth marketing) that is NOT explicitly an engineer."
sdr_bdr = "SDR, BDR, sales development, business development representative, prospecting/outbound."
sales_automation = "sales automation, RevOps tooling, outbound ops, Clay/Apollo/n8n/HubSpot specialist for sales."
revops = "Revenue Operations, Sales Operations, RevOps analyst/manager."
other = "any other opening that does not fit the above (including traditional sales, product engineering, etc.)."
+++

# Ideal role: GTM Engineer

## What the role is
A technical specialist who designs, builds and maintains the systems behind the company's
go-to-market engine. Works where sales, marketing and product meet and turns revenue strategy
into working software: builds infrastructure, not just operates tools.

## Core responsibilities
- Own the lead-to-revenue flow: capture, enrich, score and route leads automatically.
- Integrate CRM, enrichment, outbound and AI tools into unified pipelines.
- Build signal-based outbound (job changes, funding, product usage -> personalized outreach).
- CRM architecture and data hygiene (validation, dedup, reporting).
- Automation with LLMs and AI agents.

## Typical stack
HubSpot / Salesforce, Apollo / Clay, outbound tools, n8n / Zapier, APIs and webhooks,
Python / SQL, LLMs and agentic workflows, BI dashboards.

## It is NOT
- SDR or sales roles dressed up as engineering.
- Admin of a single tool, or RevOps that only tunes processes without building.
- Product software engineering without a go-to-market context; research data science.

## The candidate
A builder who comes from the commercial side: hands-on in Python, SQL, APIs and LLMs, automating
sales and marketing workflows end to end.

## Preferences
Remote from Chile (LATAM or global) or a job in Chile; tech startups or B2B SaaS; an individual
contributor role with ownership to build; English and Spanish.
