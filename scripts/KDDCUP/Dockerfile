FROM python:3.12-slim-bookworm

ARG NEO4J_VERSION=5.26.0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEO4J_HOME=/opt/neo4j \
    PATH="/opt/neo4j/bin:${PATH}" \
    NEO4J_PASSWORD=pontis_kdd_neo4j

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        gzip \
        openjdk-17-jre-headless \
        procps \
        tar \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://dist.neo4j.org/neo4j-community-${NEO4J_VERSION}-unix.tar.gz" -o /tmp/neo4j.tar.gz \
    && mkdir -p /opt \
    && tar -xzf /tmp/neo4j.tar.gz -C /opt \
    && mv "/opt/neo4j-community-${NEO4J_VERSION}" /opt/neo4j \
    && rm /tmp/neo4j.tar.gz

COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir \
        "openai>=2.30.0" \
        "pydantic>=2.12.5" \
        "pyyaml>=6.0.3" \
        "fastapi>=0.110.0" \
        "uvicorn>=0.29.0" \
        "sse-starlette>=2.0.0" \
        "datasketches>=5.0.0" \
        "neo4j>=5.28.0" \
        "sqlglot>=28.0.0"

COPY . /app
RUN chmod +x /app/scripts/KDDCUP/entrypoint.sh

ENTRYPOINT ["/app/scripts/KDDCUP/entrypoint.sh"]
