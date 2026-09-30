FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY docker/settings.yaml ./docker/settings.yaml
EXPOSE 8080
CMD ["prism", "--config", "docker/settings.yaml", "serve"]

