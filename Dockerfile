# Container image for free Python hosts that accept a Dockerfile
# (Hugging Face Spaces, Google Cloud Run, Fly.io, Koyeb, ...).
#
# The app is a plain stdlib HTTP server, so there is nothing to install.
FROM python:3.12-slim

WORKDIR /app
COPY . .

# Most container platforms inject PORT; Hugging Face Spaces expects 7860.
ENV PORT=7860
EXPOSE 7860

CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "7860"]
