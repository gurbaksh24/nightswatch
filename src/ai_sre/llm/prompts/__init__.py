"""Prompt definitions, by stage.

Every prompt module exposes:
    * `PROMPT_VERSION: str`   — bump on every change.
    * One or more prompt constants.

Prompts are pure strings. Rendering (filling in alert/context) happens at the
call site so prompts remain easy to read and diff.
"""

# Global, immutable. Bumped when any prompt changes.
PROMPT_VERSION = "0.1.0"
