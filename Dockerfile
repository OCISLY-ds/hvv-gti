FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hvv_gti ./hvv_gti
COPY map/static ./map/static

RUN useradd --system --no-create-home hvv
USER hvv

ENTRYPOINT ["python", "-m", "hvv_gti"]
CMD ["collect"]
