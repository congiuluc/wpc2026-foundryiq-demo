FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000
WORKDIR /app

# System libraries required by the ODBC driver bundled with mssql-python (INVENTORY_SOURCE=sql).
RUN apt-get update && apt-get install -y --no-install-recommends libltdl7 libkrb5-3 libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY inventory.py server.py sql_source.py ./
COPY data ./data

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
CMD ["python", "server.py"]
