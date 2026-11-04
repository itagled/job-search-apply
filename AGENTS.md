# jobradar: instructions for coding agents

jobradar finds job postings that fit one person's ideal role, scores them with an LLM against that
person's profile and helps with the outreach. The user may not read English or code: talk to them
in their language, keep it short, and do the technical steps for them.

## First run

1. **Install:** `python -m venv .venv`, then `pip install -e .[dev]` inside it.
2. **Accounts and configuration:** the user needs two accounts. Give them these links:
   - The LLM, NanoGPT (recommended: it also runs Jev, which makes the title check and the scoring
     cheaper): create the account at https://nano-gpt.com/join, add balance at
     https://nano-gpt.com/balance and create a key at https://nano-gpt.com/api. If they already use
     OpenRouter, that works too: https://openrouter.ai/sign-up, credits at
     https://openrouter.ai/settings/credits and the key at https://openrouter.ai/settings/keys.
   - Apify (the scrapers): create the account at https://console.apify.com/sign-up and copy the token
     from https://console.apify.com/settings/integrations.

   Copy `.env.example` to `.env` and have them paste `NANOGPT_API_KEY` (or `OPENROUTER_API_KEY`) and
   `APIFY_API_TOKEN` there themselves, not in the chat. The rest is optional (the README lists it); `DATABASE_URL` already
   points to a local SQLite file. `jobradar config --check` shows what is set or missing. Never print
   secret values.
3. **Database:** `alembic upgrade head` creates the tables. The default is a local SQLite file
   (`DATABASE_URL=sqlite:///data/jobradar.db`, nothing to install); Postgres works the same way.
4. **Profile:** this is the one input of the whole search. If `jobradar profile check` says
   `profile/target.md` is missing:
   - Ask the user for a job posting they like (link or text) or their ideal role in their own words.
   - Link: `jobradar profile from-url <link>`. Text: save it to a file and run
     `jobradar profile from-text <file>`. If a link needs a login but you can open it, save its text
     and use `from-text`. This uses the LLM and saves `profile/target.md`, with the original text at
     the end.
   - That file drives everything: `[search] keywords` are what every job board is searched for
     (LinkedIn, Apify and the rest) and what job titles are judged against, and the text is what each
     posting is scored against. Show the user the keywords and adjust them together: each one costs a
     search on the paid sources.
   - Ask where they live and which jobs they can take from there (remote, countries). Set
     `[location]` and `[search] locations`; for Latin America use preset `latam` (see
     `profile/target.example.md`).
   - Ask about their background and preferences, and add them to the text as `## The candidate` and
     `## Preferences`, above `## Reference posting`. Finish with `jobradar profile check`.
   - To search for something else later, repeat with another posting: the current profile is not
     overwritten, you get `profile/target.draft.md` to review and rename.
5. **CV:** if `profile/cv.md` is missing, ask for their CV (pasted text or a file you can read) and
   save it as Markdown there. The score only uses it as context.

## Daily use

- `jobradar sources list`, then `jobradar sources run --free --days 7` to load new postings. The paid
  ones are named one by one: `jobradar sources run linkedin_jobs_valig --paid --days 7`. Every run
  sends the new titles to Jev, or to a cheap LLM on OpenRouter (about US$0.002 per 1,000), and prints
  what it used.
- `jobradar fit` scores the new postings (uses the LLM); `jobradar web` shows the status page.
- `jobradar --help` lists every command; each one has its own `--help`.

## Rules

- **Paid APIs** (NanoGPT, OpenRouter, Apify, Apollo, Serper): say what a command will cost and ask before running
  it. Paid sources only run with `--paid`.
- **Messages:** never send an email or a message for the user without their explicit OK; drafts are
  fine.
- **Other people's data** (people in posts, decision makers): keep the minimum. Raw data with people expires
  (`jobradar raw --purge`).
- **Database schema:** only through Alembic migrations; review a generated migration before applying
  it, and ask before running it against a database with real data.
- Code, docs and commit messages in English. Tests: `pytest`; style: `ruff check`.
