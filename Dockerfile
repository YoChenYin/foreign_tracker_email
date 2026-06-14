FROM python:3.11-slim
LABEL "language"="python"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data && chmod 755 /app/data

EXPOSE 8080

CMD ["python", "app.py"]
