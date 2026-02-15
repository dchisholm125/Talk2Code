#!/bin/bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env file. Please edit it with your Telegram Bot Token."
fi
echo "Setup complete."
