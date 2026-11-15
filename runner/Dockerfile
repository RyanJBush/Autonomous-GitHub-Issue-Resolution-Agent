# Trusted runner image. Built by `python -m ira build-runner-image` with `docker build --network none`.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
COPY wheels/ /opt/ira-wheels/
COPY requirements.lock /opt/ira-wheels/requirements.lock
RUN python -m pip install --no-cache-dir --no-index --require-hashes --no-deps \
        --find-links /opt/ira-wheels -r /opt/ira-wheels/requirements.lock \
    && rm -rf /opt/ira-wheels \
    && python -m pytest --version
