# base image for azure functions python 3.12 v2
FROM mcr.microsoft.com/azure-functions/python:4-python3.12

# set working directory
WORKDIR /home/site/wwwroot

# copy requirements
COPY requirements.txt .

# install dependencies
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# copy the rest of the function app
COPY . .