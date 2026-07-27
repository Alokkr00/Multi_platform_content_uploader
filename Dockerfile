# Use a lightweight python image
FROM python:3.10-slim

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

# Copy the application code
COPY . .

# Expose port 8001
EXPOSE 8001

# Set environmental defaults
ENV PORT=8001
ENV HOST=0.0.0.0

# Start server
CMD ["python", "server.py"]
