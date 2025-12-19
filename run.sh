#!/usr/bin/env bash

PYTHONUNBUFFERED=1 ./telegram-daemon.py | tee daemon.log 
