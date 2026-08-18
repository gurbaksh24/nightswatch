"""ASGI middleware. Wired into the app in ``main.py``.

May import from ``utils/``, ``observability/``, and third-party code only —
middleware runs before routing, so it must not reach into ``core/`` or the DB.
"""
