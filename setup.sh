#!/bin/bash
set -e

echo "=== Setting up Python Virtual Environment ==="
if [ ! -d "venv" ]; then
    echo "Creating virtual environment 'venv'..."
    python3 -m venv venv
else
    echo "Virtual environment 'venv' already exists."
fi

echo "Activating virtual environment and installing packages..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo "=== Setup Completed Successfully! ==="
