"""Platform observability: central Prometheus metric singletons.

Like ``utils/``, this package is a *leaf*: it imports only third-party code,
never other ``ai_sre`` modules, so every layer (``api``, ``core``, ``llm``,
``delivery``, ``workers``) may import it without violating the module rules
in AGENTS.md.
"""
