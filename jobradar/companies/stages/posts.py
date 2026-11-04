"""LinkedIn monitor for the promoted companies: people → posts → LLM → alerts.

``monitor-posts`` fetches recent posts from those who usually publish vacancies, ``classify-posts``
detects which are offers and for which role, and ``promote-posts`` moves the target set to ``job_post``
so they enter fit-scoring.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from jobradar import apify, config, llm, profile
from jobradar.companies.classify_post_llm import classify_post_llm
from jobradar.companies.posts_apify import fetch_profile_posts_for_urls, map_item_to_person_post
from jobradar.companies.stages.common import PROMOTED, Failure, ids_top, in_parallel, now, process, read
from jobradar.db.models import CompanyContact, JobPost, PersonPost, TargetCompany
from jobradar.db.session import write_with_retry
from jobradar.db.upsert import NormalizedPersonPost, NormalizedPost, UpsertStats, ingest_job_post, upsert_person_post

log = logging.getLogger(__name__)

PROFILE_MONITOR_SOURCE = "linkedin_profile_monitor"
# Roles of ``company_contact`` that are monitored: they are the ones who publish vacancies.
MONITOR_ROLE_CATEGORIES = ("talent", "founder", "ceo", "c_level", "sales")
# Role types that move to ``job_post``.


def _target_buckets() -> list[str]:
    """The post role buckets that are openings for the target role (``[companies] target_buckets``)."""
    return profile.load().companies.target_buckets


def monitor_posts(
    *,
    top: int | None = None,
    order: str = "fit",
    limit: int | None = None,
    max_posts: int = 25,
    posted_limit: str = "month",
    workers: int = 6,
) -> dict[str, int]:
    """Recent posts from monitored people → ``person_post``, pending classification."""
    stmt = (
        select(CompanyContact.id, CompanyContact.linkedin_url, CompanyContact.company_id)
        .join(TargetCompany, TargetCompany.id == CompanyContact.company_id)
        .where(
            PROMOTED,
            CompanyContact.linkedin_url.is_not(None),
            CompanyContact.role_category.in_(MONITOR_ROLE_CATEGORIES),
        )
    )
    if top:
        stmt = stmt.where(CompanyContact.company_id.in_(ids_top(top, order)))
    work = read(stmt, limit)
    log.info(
        "monitor-posts: %d people, %d threads, window %s, up to %d posts each",
        len(work),
        workers,
        posted_limit,
        max_posts,
    )

    def process_one(w: Any) -> list[NormalizedPersonPost] | Failure:
        try:
            items = fetch_profile_posts_for_urls([w.linkedin_url], max_posts=max_posts, posted_limit=posted_limit)
        except apify.ApifyAuthError:
            raise  # without credentials all fail: it stops
        except Exception as exc:  # noqa: BLE001 - one person failing does not stop the rest
            return Failure(str(exc)[:120])
        posts = (map_item_to_person_post(it, contact_id=w.id, company_id=w.company_id) for it in items)
        return [p for p in posts if p is not None]

    new = refreshed = failed = 0
    for i, posts in enumerate(in_parallel(process_one, work, workers), 1):
        if isinstance(posts, Failure):
            failed += 1
            if failed <= 5:
                log.warning("monitor-posts: %s", posts.reason)
        elif posts:
            ins, upd = write_with_retry(partial(_save_posts, posts=posts))
            new += ins
            refreshed += upd
        if i % 25 == 0:
            log.info("monitor-posts: %d/%d (new posts: %d)", i, len(work), new)
    return {"people": len(work), "new_posts": new, "refreshed": refreshed, "failed": failed}


def _save_posts(session: Session, *, posts: list[NormalizedPersonPost]) -> tuple[int, int]:
    stats = UpsertStats()
    for post in posts:
        upsert_person_post(session, source=PROFILE_MONITOR_SOURCE, post=post, stats=stats)
    return stats.inserted, stats.updated


def classify_posts(*, limit: int | None = None, workers: int = 8) -> dict[str, Any]:
    """Is the post an offer? For which role and company? With the LLM (``CLASSIFY_POST_MODEL``)."""
    work = read(
        select(PersonPost.id, PersonPost.raw_content).where(
            PersonPost.enrichment_status == "pending", PersonPost.raw_content.is_not(None)
        ),
        limit,
    )
    log.info("classify-posts: %d posts, model %s, %d threads", len(work), config.get("CLASSIFY_POST_MODEL"), workers)

    def process_one(w: Any) -> tuple[int, Any] | Failure:
        res = classify_post_llm(w.raw_content)
        return Failure(res.error) if res.error else (w.id, res)

    def save(session: Session, rows: list[tuple[int, Any]]) -> None:
        for post_id, r in rows:
            session.execute(
                update(PersonPost)
                .where(PersonPost.id == post_id)
                .values(
                    is_job_post=r.is_job_post,
                    detected_role=r.detected_role,
                    role_bucket=r.role_bucket,
                    hiring_company=r.hiring_company,
                    enrichment_json={
                        "confidence": r.confidence,
                        "detected_role": r.detected_role,
                        "role_bucket": r.role_bucket,
                        "hiring_company": r.hiring_company,
                    },
                    enrichment_status="done",
                    enriched_at=now(),
                )
            )

    return {**process("classify-posts", work, process_one, save, workers, warm_up=True), "llm": llm.usage_total()}


def promote_posts() -> dict[str, int]:
    """Move detected offers from the target set to ``job_post``, for fit-scoring."""
    rows = read(
        select(
            PersonPost.id,
            PersonPost.external_id,
            PersonPost.post_url,
            PersonPost.detected_role,
            PersonPost.hiring_company,
            PersonPost.raw_content,
            PersonPost.posted_at,
            PersonPost.contact_id,
            PersonPost.company_id,
            PersonPost.role_bucket,
        ).where(
            PersonPost.is_job_post.is_(True),
            PersonPost.role_bucket.in_(_target_buckets()),
            PersonPost.job_post_id.is_(None),
        )
    )
    log.info("promote-posts: %d target offers", len(rows))
    for row in rows:
        write_with_retry(partial(_promote, row=row))
    return {"promoted": len(rows)}


def _promote(session: Session, *, row: Any) -> None:
    post = NormalizedPost(
        external_id=row.external_id,
        post_url=row.post_url,
        title=row.detected_role,
        company=row.hiring_company,
        posted_at=row.posted_at,
        raw_content=row.raw_content,
        source_metadata={
            "monitor": PROFILE_MONITOR_SOURCE,
            "role_bucket": row.role_bucket,
            "person_post_id": row.id,
            "contact_id": row.contact_id,
            "company_id": row.company_id,
        },
    )
    job_id, _ = ingest_job_post(session, source=PROFILE_MONITOR_SOURCE, post=post, stats=UpsertStats())
    # Fit-scoring requires a description: the post text is used if the posting does not have one.
    session.get(JobPost, job_id).set_description(row.raw_content)
    session.execute(update(PersonPost).where(PersonPost.id == row.id).values(job_post_id=job_id))


def posts_report() -> dict[str, Any]:
    """Target vacancies detected in posts, from newest to oldest."""
    rows = read(
        select(
            PersonPost.role_bucket,
            PersonPost.detected_role,
            PersonPost.hiring_company,
            PersonPost.post_url,
            TargetCompany.name.label("company_name"),
            CompanyContact.full_name.label("author"),
        )
        .join(TargetCompany, TargetCompany.id == PersonPost.company_id, isouter=True)
        .join(CompanyContact, CompanyContact.id == PersonPost.contact_id, isouter=True)
        .where(PersonPost.is_job_post.is_(True), PersonPost.role_bucket.in_(_target_buckets()))
        .order_by(PersonPost.posted_at.desc())
    )
    return {
        "openings": [
            {
                "role_bucket": f.role_bucket,
                "title": f.detected_role,
                "company": f.hiring_company or f.company_name,
                "author": f.author,
                "url": f.post_url,
            }
            for f in rows
        ]
    }
