<div align="center">

# Linger

**Real-time English captions for any audio playing on your computer — designed to stay still long enough to read, select, and copy.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6.svg)
![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-3776AB.svg)

**English** · [简体中文](README.zh-CN.md)

</div>

![Linger in dark mode](docs/ui-dark.png)

## Overview

Linger captures the audio your computer is playing — from a video call, an online lecture, a webinar, or a recorded video — and transcribes it into English text in real time using the [ElevenLabs Scribe](https://elevenlabs.io/speech-to-text) realtime speech-to-text model.

Most live-caption tools are designed to be glanced at: text scrolls past and disappears. Linger is designed to be **worked with**. Finalized text is never rewritten, the view never moves while you are reading or selecting, and any passage can be copied with a single keystroke from any application — to look up an unfamiliar term, for instance, or to paste a sentence into a translator.

## Features

- **System audio capture** — Records any output device through WASAPI loopback. No virtual audio cable, additional driver, or change to other applications' audio settings is required, and microphone input is never captured.
- **Stable, append-only transcript** — Finalized text is written once and never modified. Only the line currently being recognized updates, and it pauses while it is selected.
- **Segment-level selection** — Click the timestamp gutter to select a whole segment; extend the selection with <kbd>Shift</kbd>, toggle segments with <kbd>Ctrl</kbd>, or drag across several. Free-form text selection across segments is also supported.
- **Global copy hotkey** — Press <kbd>Ctrl</kbd>+<kbd>`</kbd> in any application to copy the most recent utterance. Press again within two seconds to include one more preceding segment.
- **Translator-friendly output** — Multiple segments are joined into a single line, so a sentence interrupted by a pause remains one sentence when pasted.
- **Automatic reconnection** — Transient network and service interruptions are recovered from automatically, and each gap is marked in the transcript.
- **Automatic saving** — Every segment is appended to a timestamped text file as soon as it is finalized.
- **Self-test** — Verifies audio capture and API credentials before a session.
- **Light and dark themes**, adjustable text size, and an always-on-top mode.

## Requirements

- Windows 10 or Windows 11
- Python 3.13 or later
- An [ElevenLabs](https://elevenlabs.io) API key with access to realtime speech-to-text

## Installation

```powershell
git clone https://github.com/BocchiJamSuki/linger.git
cd linger
.\setup.cmd
```

`setup.cmd` creates a virtual environment in `.venv` and installs the dependencies listed in `requirements-dev.txt`.

## Configuration

Linger reads the API key from the `ELEVENLABS_API_KEY` variable, checking the following locations in order:

1. The environment of the running process
2. Windows user and system environment variables, including values set after the application was launched
3. A `.env` (or `.env.txt`) file in the project directory:

   ```dotenv
   ELEVENLABS_API_KEY=your-api-key
   ```

## Usage

Launch Linger with **`Start Linger.cmd`**, or from a terminal:

```powershell
.\.venv\Scripts\python.exe main.py
```

1. Select an audio source in the toolbar. **系统默认** (*System default*) follows the Windows default output device. If the application you want to transcribe plays through a specific device, select that device instead.
2. Click **开始** (*Start*). A connection to ElevenLabs is opened only at this point; launching the application does not capture audio or incur usage.
3. Click **停止** (*Stop*) to end the session. Words not yet finalized are retained and shown in italics.

Before an important session, choose **⋯ → 运行自检** (*Run self-test*) to confirm that audio is being captured and that the API key is valid. Enabling Windows *Do Not Disturb* prevents notification sounds from being transcribed.

### Selecting and copying text

Speech is segmented at natural pauses rather than at sentence boundaries, so a single sentence — or a later correction to it — may span several segments. Linger offers several ways to capture exactly what is needed:

| To copy | Action |
| --- | --- |
| The most recent utterance | Press <kbd>Ctrl</kbd>+<kbd>`</kbd> in any application. Press again to reach one segment further back. |
| One or more whole segments | Click a timestamp. <kbd>Shift</kbd>+click selects a range, <kbd>Ctrl</kbd>+click adds or removes a segment, and dragging along the gutter selects consecutive segments. |
| Any span of words | Drag across the text, including across segments. |
| The entire transcript | **⋯ → 复制全部内容** (*Copy all*) |

While a selection exists, or after scrolling up, the view stops following new text. A **回到最新** (*Jump to latest*) button appears for returning to the live position.

### Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| <kbd>Ctrl</kbd>+<kbd>`</kbd> | Copy the most recent utterance (system-wide) |
| <kbd>Ctrl</kbd>+<kbd>C</kbd> | Copy the current selection |
| <kbd>Esc</kbd> | Clear the selection |
| <kbd>Ctrl</kbd>+<kbd>=</kbd> / <kbd>Ctrl</kbd>+<kbd>-</kbd> | Increase / decrease text size |

The system-wide hotkey can be changed to <kbd>Alt</kbd>+<kbd>`</kbd>, <kbd>F8</kbd>, or <kbd>F9</kbd>, or disabled, under **⋯ → 全局复制快捷键** (*Global copy hotkey*).

## Reliability

- **Reconnection.** When a connection drops, Linger reconnects immediately and then with increasing back-off. Words still being recognized at the time are preserved and marked as unconfirmed, and the transcript records the length of the interruption. A session stops only on errors that retrying cannot resolve, such as an invalid key or an exhausted quota.
- **Latency control.** If captured audio falls more than 500 ms behind, or a single transmission takes longer than one second, stale audio is discarded and the stream resynchronizes instead of drifting further behind.
- **Continuous streaming.** Audio is streamed for the full duration of a session, including silences, so the beginning of the next utterance is never lost to a reconnection delay.

## Data and privacy

- Audio is sent to ElevenLabs only while a session is running. Launching the application, selecting a device, and the audio portion of the self-test are entirely local.
- The API key is held in memory and used only in the connection request header. It is never written to logs, files, or URLs.
- Transcripts are saved locally to `Documents\Linger\` as timestamped UTF-8 text files. No other data is stored or transmitted.
- Preferences (audio source, theme, text size, hotkey, and window position) are stored in the Windows registry under `HKEY_CURRENT_USER\Software\Linger`.

## Limitations

- Windows only; audio capture relies on WASAPI loopback.
- Transcription language is fixed to English.
- The user interface is currently available in Simplified Chinese only.
- Recognition accuracy, latency, and pricing are determined by the ElevenLabs service.

## Development

```text
linger/
├── audio.py        WASAPI loopback capture, mono PCM16 conversion, 100 ms packetization
├── scribe.py       Credentials, connection parameters, event decoding, error classification
├── engine.py       Worker thread: streaming, reconnection, self-test
├── transcript.py   Segment model, utterance grouping, autosave
├── view.py         Transcript view: append-only stream, gutter selection, follow behaviour
├── hotkey.py       System-wide hotkey (Win32 RegisterHotKey)
├── window.py       Main window
└── theme.py        Palettes, stylesheet, icons, title bar tinting
```

Run the test suite (network access is restricted to localhost):

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Regenerate the screenshots in `docs/`:

```powershell
.\.venv\Scripts\python.exe tools\preview.py
```

Transcription uses the `scribe_v2_realtime` model with voice-activity-based commits (0.7 s silence threshold). Audio is transmitted as 48 kHz mono 16-bit PCM in 100 ms chunks.

## License

Released under the [MIT License](LICENSE).
