FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install uv==0.5.3
RUN uv pip install --system --no-cache-dir -r requirements.txt

# Copy source
COPY . .

ENV PORT=8080

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
