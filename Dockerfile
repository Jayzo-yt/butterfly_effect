FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

# Runtime deps only — networkx/fastapi/uvicorn. osmnx/geopandas (ingestion-only,
# see scripts/ingest_manipal.py) are never installed here; the real-city data
# is pre-baked into data/city/manipal.json at image build time, not fetched
# from OSM at container start.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY data/ data/
COPY --from=frontend-build /frontend/dist/ frontend/dist/

ENV CITY_DATA_PATH=data/city/manipal.json
EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
