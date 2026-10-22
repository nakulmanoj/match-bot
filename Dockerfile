FROM python:3.11-slim

# Install system dependencies needed for OpenCV / EasyOCR / Matplotlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Set up non-root user (Hugging Face Spaces runs as UID 1000)
RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /home/user/app

# Install Python dependencies as user
COPY --chown=user:user requirements.txt .
USER user
RUN pip install --no-cache-dir --user -r requirements.txt

# Pre-download EasyOCR english model during build so startup is instant
RUN python -c "import easyocr; easyocr.Reader(['en'], gpu=False)"

# Copy application source code
COPY --chown=user:user . .

# Hugging Face Spaces listens on port 7860
EXPOSE 7860

# Run the Discord bot
CMD ["python", "bot.py"]
