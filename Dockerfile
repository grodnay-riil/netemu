FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        iproute2 \
        iptables \
        iputils-ping \
        kmod \
        network-manager \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY netemu_core.py app.py ./
RUN mkdir -p /app/profiles

EXPOSE 8501

HEALTHCHECK CMD curl -f http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false", \
     "--server.fileWatcherType=poll"]
