# python run-time as a parent image
FROM python:3.11-slim

# Install system dependencies required by unstructured, pdf2image, pillow
RUN apt-get update && apt-get install -y \
    build-essential \
    libmagic1 \
    poppler-utils \
    tesseract-ocr \
    libtesseract-dev \
    libxml2 \
    libxslt1.1 \
    libgl1 \
    ffmpeg \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# working directory in container
WORKDIR /app

# copying the dependencies to the working directory(.)
COPY requirements.txt .

RUN pip install --timeout=1000 --no-cache-dir -r requirements.txt

# copy the code 
COPY . .

# port for uvicorn
EXPOSE 8000

# run the server, for all network interfaces
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
