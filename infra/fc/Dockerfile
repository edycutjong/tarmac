# Alibaba Function Compute 3.0 — custom container for Tarmac.
# python:3.12-slim: Tarmac needs >=3.11 (enum.StrEnum) and declares >=3.12.
FROM python:3.12-slim

WORKDIR /app
COPY . /app

# pynacl / pydantic-core ship manylinux wheels, so this is wheel-only (no toolchain).
RUN pip install --no-cache-dir -e .

# FC injects the listen port as FC_SERVER_PORT; the handler serves on it.
ENV FC_SERVER_PORT=9000
EXPOSE 9000
CMD ["python", "-m", "infra.fc.handler"]
