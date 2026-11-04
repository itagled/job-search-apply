"""Funnel pipeline to build the database of target companies.

Stages (CLI in ``jobradar/companies/run.py``; the code for each, in ``stages/``):
    extract → screen → enrich-base → rescreen → classify → contacts
with human review gates (export/import xlsx) in between.
"""
