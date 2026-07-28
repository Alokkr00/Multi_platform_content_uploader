FROM python:3.12-slim

# Install system dependencies (ffmpeg is required for video transcoding)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port 8000
EXPOSE 8000

# Environment defaults
ENV PORT=8000
ENV HOST=0.0.0.0

CMD ["python", "server.py"]
