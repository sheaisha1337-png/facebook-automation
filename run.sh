#!/bin/bash

# Ensure setup has run
if [ ! -d "venv" ]; then
    echo "Virtual environment not found. Running setup.sh first..."
    bash setup.sh
fi

echo "Activating virtual environment..."
source venv/bin/activate

# Open browser after a slight delay so flask can start up
echo "Opening application dashboard in browser..."
(sleep 2 && open http://127.0.0.1:5005) &

echo "Starting Flask web server on port 5005..."
python3 app.py
