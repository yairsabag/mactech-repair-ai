FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all source files
COPY *.py ./
COPY app.html ./

# Copy board data from repo to expected location
COPY repair_ai_db/ /root/repair_ai_db/

# Environment
ENV PORT=8765

EXPOSE 8765

CMD ["python3", "start_server.py"]
