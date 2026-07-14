# Vault API. Built on the Pi (ARM64) by docker compose; python:3.12-slim is
# multi-arch so no cross-build is needed.
FROM python:3.12-slim

WORKDIR /app

# Dependencies first so a code change doesn't re-resolve pip.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/vault_api/

# Run as a non-root user. The vault is mounted read-only anyway, but the
# container has no reason to hold root.
RUN useradd --create-home --uid 1001 vaultapi
USER vaultapi

EXPOSE 8200

# Host/port come from config.py defaults; VAULT_API_PORT is honoured by compose.
CMD ["uvicorn", "vault_api.app:app", "--host", "0.0.0.0", "--port", "8200"]
