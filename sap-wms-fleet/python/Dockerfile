FROM python:3.12-slim
WORKDIR /app
COPY chain ./chain
COPY tests ./tests
ENV HOST=0.0.0.0 PYTHONUNBUFFERED=1
EXPOSE 9004 9120 9200 9300
CMD ["python", "-m", "chain"]
