"""The stages of the company funnel, one module per stage. The CLI is in ``jobradar.companies.run``.

Each stage reads its work list in a short session, calls the API in parallel, and saves the
results in batches, each batch in its own transaction (see ``common.py``).
"""
