FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps: ffmpeg for transform/compile, curl for downloads, fonts for endcard
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/firebot

# Install fpcalc (chromaprint) standalone binary
RUN curl -sL -o /tmp/chromaprint.tar.gz \
      https://github.com/acoustid/chromaprint/releases/download/v1.5.1/chromaprint-fpcalc-1.5.1-linux-x86_64.tar.gz \
    && tar xzf /tmp/chromaprint.tar.gz -C /tmp/ \
    && mv /tmp/chromaprint-fpcalc-1.5.1-linux-x86_64/fpcalc /usr/local/bin/fpcalc \
    && chmod +x /usr/local/bin/fpcalc \
    && rm -rf /tmp/chromaprint*

# Install gallery-dl Linux binary (auto-bundles its python deps)
RUN curl -sL -o /usr/local/bin/gallery-dl \
      https://codeberg.org/mikf/gallery-dl/releases/download/v1.32.1/gallery-dl.bin \
    && chmod +x /usr/local/bin/gallery-dl

# fpcalc path differs in container vs laptop — symlink to where the code expects
RUN mkdir -p /root/.local/bin && ln -s /usr/local/bin/fpcalc /root/.local/bin/fpcalc

# Python deps
COPY pyproject.toml .
RUN pip install --root-user-action=ignore \
    google-auth \
    google-auth-oauthlib \
    google-api-python-client \
    google-genai \
    requests

# App code
COPY *.py ./

# Default font path in container (fonts-dejavu-core puts it here)
ENV FONT_PATH=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf

CMD ["python", "main.py", "run"]
