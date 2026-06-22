---
title: Facebook Bypass Tool
emoji: 🚀
colorFrom: indigo
colorTo: purple
sdk: docker
pinned: false
license: mit
short_description: AI Voice & video suite with copyright-safe tools
---

# 🚀 Jigarzzz❤️ — Premium Video Suite

> **Unlimited Free Voice generation, video generation and Anti copyright yt**

A powerful all-in-one video creation and safety editing tool built with Flask + FFmpeg + edge-tts.

## ✨ Features

- 🎙️ **AI Voice Generation** — 300+ voices, 50+ languages via edge-tts (Microsoft Neural TTS)
- 🎬 **Video Merger** — Merge multiple clips with voiceover, background music & aspect ratio control
- ✂️ **YouTube Safe Clipper** — Apply copyright-safe filters to your videos
- 🤖 **AI Video Generator** — Script-to-video with auto imagery and narration
- 🧑 **Voice Age Filter** — Child, Teen, Adult, Aged voice simulation
- 🎭 **Mood & Style Presets** — Calm, Excited, Professional, Dramatic and more

## 🛠️ Tech Stack

- **Backend:** Python / Flask
- **TTS:** edge-tts (Microsoft Neural voices, free)
- **Video:** FFmpeg + MoviePy
- **Frontend:** Vanilla HTML/CSS/JS (no framework)

## 🚀 How to Use

1. Open the app
2. Choose a tab: **Video Merger**, **YT Safe Clipper**, or **AI Video Generator**
3. Upload your videos / write your script
4. Select voice, language, mood, and age
5. Hit Generate — download your video!

## 💻 Local Setup Instructions

### 1. Install System Dependencies (FFmpeg)
This software requires **FFmpeg** to process videos, add filters, and merge audio.

* **Windows:**
  1. Open PowerShell as **Administrator** and run:
     ```powershell
     winget install Gyan.FFmpeg
     ```
  2. Restart your terminal window to apply the change to your PATH.
* **macOS:**
  ```bash
  brew install ffmpeg
  ```
* **Linux (Ubuntu/Debian):**
  ```bash
  sudo apt update && sudo apt install -y ffmpeg
  ```

### 2. Set Up Python Environment
1. Ensure you have Python 3.9 - 3.11 installed.
2. Open your command prompt/terminal inside the project folder:
   * **Windows (Command Prompt):**
     ```cmd
     python -m venv venv
     venv\Scripts\activate
     ```
   * **macOS / Linux:**
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```

### 3. Install Python Dependencies
With your virtual environment activated, run:
```bash
pip install -r requirements.txt
```
*(Note: On Windows, `gunicorn` is not supported and will not run, but the app uses Flask's built-in server or Waitress locally, which works perfectly).*

### 4. Run the Application
Start the local server:
```bash
python app.py
```
Open your web browser and go to:
👉 **[http://localhost:5005](http://localhost:5005)**

## ⚠️ Notes

- Files are stored temporarily and cleared on Space restart / server restart.
- Video processing can take 30–120 seconds depending on length.
- Use short scripts (under 500 words) for best performance on free tier.