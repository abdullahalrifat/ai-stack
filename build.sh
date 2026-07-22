#!/bin/bash

### Remobe OLD images ###

docker rmi ai-agents:latest
docker rmi custom-litellm:latest

### Build NEW images ###
docker build --no-cache -t ai-agents:latest ./agents
docker build --no-cache -t custom-litellm:latest ./litellm


