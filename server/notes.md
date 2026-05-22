# Clio Server — File Summaries

## __init__.py
Empty file. Marks the `server` folder as a Python package.

## agent.py
The core brain of Clio. Runs the streaming conversation loop with Claude, handles tool calls, requests user permission for sensitive tools, and streams synthesized audio back to the phone sentence by sentence.

## main.py
The web server. Serves the phone UI as static files, handles WebSocket connections from the phone, and routes incoming audio and permission responses to the agent.

## session.py
Tracks a single voice session. Writes a timestamped markdown log file of every exchange between the user and Claude, and records when the session ends.

## stt.py
Speech to text. Uses the Whisper model (via faster-whisper) to transcribe audio recordings into text, with voice activity detection to filter out silence and breathing.

## tools.py
Defines all the tools Clio can use. Specifies which tools are auto-approved and which require phone approval, provides human-readable descriptions for permission prompts, and implements each tool (read file, write file, edit file, bash command, delete file, update memory, restart server).

## tts.py
Text to speech. Uses the Piper voice model to synthesize spoken audio from text and write it to a WAV file.
