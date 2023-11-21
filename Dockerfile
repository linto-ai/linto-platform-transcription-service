FROM python:3.10
LABEL maintainer="rbaraglia@linagora.com"

WORKDIR /usr/src/app

RUN apt-get update && apt-get -y install ffmpeg

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY transcriptionservice /usr/src/app/transcriptionservice
COPY docker-entrypoint.sh wait-for-it.sh ./
COPY supervisor /usr/src/app/supervisor
RUN mkdir -p /var/log/supervisor/
RUN mkdir /usr/src/app/logs
RUN chmod +x docker-entrypoint.sh wait-for-it.sh

ENV PYTHONPATH="${PYTHONPATH}:/usr/src/app/transcriptionservice"

HEALTHCHECK CMD curl localhost:80/healthcheck

EXPOSE 80

ENTRYPOINT ["./docker-entrypoint.sh"]

