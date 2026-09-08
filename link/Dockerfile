FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY webtracks_link ./webtracks_link
RUN pip install --no-cache-dir .
ENV LINK_HOST=0.0.0.0 LINK_PORT=8770
EXPOSE 8770
HEALTHCHECK CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8770/healthz').status==200 else 1)"
CMD ["python", "-m", "webtracks_link"]
