FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies (build-essential, curl for health checks if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Run the bot
CMD ["python", "bot.py"]
