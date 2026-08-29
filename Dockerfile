# Lyte Services Space — BIND_AS_A11OY_PACKAGE. Not a flagship.
FROM mirror.gcr.io/library/python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860
COPY a11oy_factory ./a11oy_factory
COPY space/server.py ./server.py
COPY space/index.html ./index.html
EXPOSE 7860
CMD ["python", "-u", "server.py"]
