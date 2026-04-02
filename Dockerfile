FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY steam_sign_out_worker/package*.json ./steam_sign_out_worker/
RUN npm install --prefix steam_sign_out_worker

COPY . .

RUN mkdir -p /app/data && chmod +x /app/run_all.sh

CMD ["sh", "/app/run_all.sh"]