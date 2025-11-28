#!/bin/sh

# This script starts the server in the background, pulls models,
# and then waits for the server process to exit.

# Start the Ollama server in the background
echo "Starting Ollama server in background..."
ollama serve &

# Get the process ID of the server
OLLAMA_PID=$!

# Wait for the server to be up and running
echo "Waiting for Ollama server to start..."
while ! curl -s -f http://localhost:11434 > /dev/null; do
    sleep 1
done
echo "Ollama server is running."

# [cite_start]Pull the required models [cite: 4]
echo "Pulling embedding model..."
ollama pull nomic-embed-text

echo "Pulling LLM..."
ollama pull llama3:instruct

echo "Models have been pulled."
echo "Ollama is now running with the required models."

# Wait for the Ollama server process to exit.
# This keeps the container running.
wait $OLLAMA_PID