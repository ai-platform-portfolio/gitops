FROM python:3.12.12-slim@sha256:f3fa41d74a768c2fce8016b98c191ae8c1bacd8f1152870a3f9f87d350920b7c

WORKDIR /app
COPY cluster-onboarding/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home worker

COPY cluster-onboarding/onboard.py ./onboard.py
COPY scripts/credentials.py ./credentials.py
USER 10001
ENTRYPOINT ["python", "/app/onboard.py"]
