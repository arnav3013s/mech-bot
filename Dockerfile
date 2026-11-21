FROM python:3.13-slim
WORKDIR /bot
COPY mechfight.py survivor.py tactics.py ./
ENTRYPOINT ["python3", "-u", "/bot/survivor.py"]
