# The official browser image and Python package must use the same Playwright release.
FROM mcr.microsoft.com/playwright:v1.62.0-noble@sha256:baed2032d533817f3dbe6425de795788430ba345e819a1201337009ba17c9d07
RUN curl --fail --silent --show-error https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py \
    && python3 /tmp/get-pip.py --break-system-packages pip==25.3 \
    && python3 -m pip install --break-system-packages --no-cache-dir pytest==8.4.2 pytest-playwright==0.7.2 playwright==1.62.0 \
    && rm /tmp/get-pip.py \
    && ln -s /usr/bin/python3 /usr/local/bin/python
COPY requirements.lock /tmp/requirements.lock
RUN python3 -m pip install --break-system-packages --no-cache-dir -r /tmp/requirements.lock
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
USER 65532:65532
WORKDIR /workspace
