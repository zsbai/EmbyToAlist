FROM python:3.13-alpine

WORKDIR /app

COPY . /app

# Install requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Keeps Python from generating .pyc files in the container
ENV PYTHONDONTWRITEBYTECODE=1

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

# Creates a non-root user with an explicit UID and adds permission to access the /app folder
RUN adduser -u 1145 --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

CMD ["python", "main.py"]