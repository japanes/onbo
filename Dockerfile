FROM python:3.11-slim

WORKDIR /app
COPY . /app

# Install with the full extras so all channels/RAG/STT are available in the image.
RUN pip install --no-cache-dir ".[all]"

CMD ["onbo", "serve", "web"]
