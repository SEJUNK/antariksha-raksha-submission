# ANTARIKSHA-RAKSHA backend image (FastAPI + SQLite + SGP4) for Railway or any
# container host. The frontend is NOT built here (it is deployed on Vercel).
#
# Persistent state lives on a mounted volume (Railway: mount path /data):
#   ANTARIKSHA_DB_PATH=/data/antariksha.db
#   ANTARIKSHA_TLE_CACHE_DIR=/data/tle_cache
# Exactly ONE process: the internal scheduler and the refresh/demo locks are
# in-process, so never run more than one worker or one replica.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/ backend/
# Only the committed working set (protected-asset seed); never a database.
COPY data/working_set.json data/working_set.json

# Railway injects PORT; 8000 is the local default. --workers 1 is required.
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
