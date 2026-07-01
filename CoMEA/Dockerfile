FROM ubuntu:24.04

RUN apt update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    tzdata python3 python3-pip python3-venv npm gettext

WORKDIR /app

COPY ./comea_website /app

RUN python3 -m venv /app && /app/bin/pip3 install -r Requirements.txt

CMD [ "/bin/bash", "/app/entrypoint.sh" ]
