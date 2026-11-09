FROM python:3.12-slim
WORKDIR /app
COPY server.py index.html ./
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "server.py"]
