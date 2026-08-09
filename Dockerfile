# A container is the honest way to run this: it keeps a filesystem, so sessions
# and the corpus survive, and it can carry the part of Zephyr the app reads.
#
# That part is small. The app reads devicetree bindings and SoC .dtsi files --
# `dts/` -- and nothing else: 23 MB against a 6.4 GB west workspace. So a
# sparse checkout gives a deployed instance the *same* checks a developer
# machine has, rather than the degraded ones a host without it would run.
FROM python:3.12-slim AS zephyr

ARG ZEPHYR_REF=v4.4.2
RUN apt-get update && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch ${ZEPHYR_REF} --filter=blob:none --sparse \
      https://github.com/zephyrproject-rtos/zephyr /zephyr \
 && cd /zephyr && git sparse-checkout set dts


FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ core/
COPY agents/ agents/
COPY codegen/ codegen/
COPY services/ services/
COPY webapp/ webapp/

# Pinned to the same ref the binding index was captured from. If these ever
# drift, a compatible verified against one is not verified against the other.
COPY --from=zephyr /zephyr /zephyr
ENV ZEPHYR_BASE=/zephyr

# Sessions and the corpus. Mount a volume here or they go when the container
# is replaced -- the app reports which of the two is happening.
ENV FWAGENT_DATA=/data
VOLUME /data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["uvicorn", "webapp.api:app", "--host", "0.0.0.0", "--port", "8000"]
