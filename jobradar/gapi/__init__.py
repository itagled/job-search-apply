"""Google (Gmail, Calendar and Drive) with a single credential handling.

Everything goes through ``googleapiclient`` and the token profiles from ``jobradar.gapi.auth``.
The package is intentionally not named ``google``: it would shadow the ``google-auth`` library.
"""
