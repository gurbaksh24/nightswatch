# `scripts/`

One-off and ops scripts. Anything that isn't part of the running service.

Conventions:
- Python scripts use `#!/usr/bin/env python` and are runnable with
  `python -m scripts.<name>` or directly.
- Each script has a module docstring describing what it does, who runs it,
  and how often.
- Shared utilities live in `ai_sre.utils`, not here.
