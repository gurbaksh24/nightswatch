# AI-SRE application image. One image runs as either the API or the worker,
# selected by command (see fly.toml [processes] / HLD §12).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install the package (hatchling build; all runtime deps ship wheels for
# py3.12-slim, so no build toolchain is needed).
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# Alembic needs the ini + migration scripts at runtime (release command).
COPY alembic.ini ./
COPY migrations ./migrations
COPY ops/fly/release.sh ./ops/fly/release.sh

RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

# Default: the API. The worker overrides this with
# `python -m ai_sre.workers.investigation_worker`.
CMD ["uvicorn", "ai_sre.main:app", "--host", "0.0.0.0", "--port", "8000"]
