# Vault API. Built on the Pi (ARM64) by docker compose; python:3.12-slim is
# multi-arch so no cross-build is needed.
FROM python:3.12-slim

# Write endpoints commit and push to the vault repo over SSH.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so a code change doesn't re-resolve pip.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/vault_api/

# Run as a non-root user. UID 1000 deliberately matches kezman554 on the Pi:
# the bind-mounted vault (and the deploy key mounted at ~/.ssh) belong to that
# UID, so git can read the key and write the repo without ownership tricks.
RUN useradd --create-home --uid 1000 vaultapi
USER vaultapi

EXPOSE 8200

# Host/port come from config.py defaults; VAULT_API_PORT is honoured by compose.
CMD ["uvicorn", "vault_api.app:app", "--host", "0.0.0.0", "--port", "8200"]
