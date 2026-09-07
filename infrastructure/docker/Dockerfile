FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GRADIO_ANALYTICS_ENABLED=false
RUN groupadd --gid 10001 bank && useradd --uid 10001 --gid bank --create-home bank
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=bank:bank . .
RUN mkdir -p /app/.local && chown bank:bank /app/.local
USER bank
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "apps.banking_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
