FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all source files
COPY *.py ./
COPY app.html ./

# Create data directories
RUN mkdir -p /data/repair_ai_db

# Environment
ENV PORT=8765
ENV REPAIR_AI_DB=/data/repair_ai_db

EXPOSE 8765

CMD ["python3", "start_server.py"]
