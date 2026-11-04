from __future__ import annotations

import importlib.util

from jobradar import cli
from jobradar.companies import heuristics
from jobradar.sources.linkedin_alerts import canonical_job_url, extract_jobs_from_html


def test_linkedin_alert_emails_yield_postings_in_order():
    html = """
    <table>
      <tr><td><a href="https://www.linkedin.com/comm/jobs/view/4300123456/?trackingId=abc&refId=x">
          <img src="logo.png"></a></td>
          <td><a href="https://www.linkedin.com/comm/jobs/view/4300123456/?trk=eml">GTM Engineer</a>
              <p>Acme · Remote</p></td></tr>
      <tr><td><a href="https://www.linkedin.com/e/v2?url=https%3A%2F%2Fwww.linkedin.com%2Fjobs%2Fview%2F4300999999%2F">
          Revenue Operations Analyst</a></td></tr>
      <tr><td><a href="https://www.linkedin.com/jobs/search/?keywords=gtm">See all jobs</a></td></tr>
    </table>"""
    jobs = extract_jobs_from_html(html)
    assert [(j.job_id, j.title) for j in jobs] == [
        ("4300123456", "GTM Engineer"),
        ("4300999999", "Revenue Operations Analyst"),
    ]
    assert jobs[0].url == canonical_job_url("4300123456") == "https://www.linkedin.com/jobs/view/4300123456/"
    assert extract_jobs_from_html("") == []


def test_company_heuristics_follow_the_profile_keywords():
    saas = heuristics.score_cheap(name="Acme", industry="B2B SaaS fintech", employee_count=60)
    mining = heuristics.score_cheap(name="Minera Norte", industry="Mining", employee_count=60)
    assert saas.tier == "high" and "saas" in saas.signals["high_industry"]
    assert mining.tier == "low" and mining.score < saas.score


def test_the_command_lists_its_subcommands_and_rejects_unknown_ones(capsys):
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert all(name in out for name in ("sources", "fit", "companies", "profile", "web"))
    assert cli.main(["nope"]) == 2
    assert all(importlib.util.find_spec(module) for module, _ in cli.COMMANDS.values())
