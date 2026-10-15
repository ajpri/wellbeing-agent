# Wellbeing Agent

A personal AI companion that checks in on your day, journals it, and draws a cartoon of your highlights. Built over a weekend because I wanted something more personal than a notes app.

![Dashboard](docs/dashboard.png)

## What it does

You chat with the agent about your day — what happened, how you're feeling. It asks a couple of follow-up questions (like a friend, not a therapist), then saves a structured journal entry and generates a cute cartoon illustration of your highlights using Gemini.

Over time you build up a collection of daily entries you can browse, search, and share.

**The search is the cool part** — it uses FAISS vector search with sentence embeddings, so searching "green tea" will match entries where you talked about matcha. It understands meaning, not just keywords.

## Screenshots

| Entries | Journal Entry |
|---------|--------------|
| ![Entries](docs/entries.png) | ![Journal](docs/journal.png) |

| Calendar | Chat |
|----------|------|
| ![Calendar](docs/calendar.png) | ![Chat](docs/chat.png) |

## Features

- **AI check-in chat** — conversational daily journaling powered by Gemini via Google ADK
- **Auto-generated cartoons** — each entry gets a cartoon illustration of your day
- **Semantic search** — FAISS + sentence-transformers, so "feeling stressed" finds entries about anxiety too
- **Mood tracking** — color-coded mood rings on the dashboard, streak counter
- **Calendar view** — month view with mood indicators and entry previews
- **Share cards** — generates a nice image card from any entry, ready for stories/social
- **Keyboard shortcuts** — Cmd+K to search, arrow keys to navigate between entries

## Tech stack

- **Agent**: [Google ADK](https://google.github.io/adk-docs/) with Gemini 3 Flash
- **Image generation**: Gemini 3 Pro (image preview)
- **Search**: FAISS + all-MiniLM-L6-v2 sentence embeddings
- **Backend**: FastAPI + Uvicorn
- **Frontend**: React 19, Vite 8, Tailwind CSS 4, Framer Motion, Recharts
- **Data**: Markdown files (no database needed for journals)

## Getting started

### Prerequisites

- Python 3.12+
- Node.js 22.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- A [Google AI Studio](https://aistudio.google.com/) API key

### Get a Gemini API key

These steps apply to both manual setup and the Linux launcher:

1. Open [Google AI Studio's API Keys page](https://aistudio.google.com/apikey)
   and sign in with your Google account.
2. Create an API key for your Google Cloud project, or use the default project
   provided to new AI Studio users. If your project is missing, import it in
   AI Studio's Projects page first.
3. Copy the key. During setup, save it as `GEMINI_API_KEY` in your local `.env`
   file. The Linux launcher can offer to save it for you through a hidden prompt.

See [Google's API key guide](https://ai.google.dev/gemini-api/docs/api-key) for
project and permission details. Keep `.env` private; it is ignored by Git.
Existing exported environment variables take precedence over `.env`. Google's
client prefers `GOOGLE_API_KEY` if both key names are set.

### Setup

#### Linux launcher

On Debian or Fedora (including derivatives such as Ubuntu and Pop!_OS), use the
Linux development launcher from the repository root:

```bash
./startLinux.sh
```

It reports missing dependencies and shows the installation plan before asking
`Install these dependencies now? [y/N]`. System packages use `apt-get` or `dnf`
with `sudo`; everything else runs as your normal user. Missing uv is installed
from Astral's official installer, Python 3.12 is managed by uv, and missing or
incompatible Node.js/npm is installed from the latest official Node.js 22 binary
with SHA256 verification. Node is stored under
`~/.local/share/wellbeing-agent/node`; shell profiles and system runtimes are not
modified. Automatic Node installation supports x86_64 and aarch64 Linux.

If `.env` is missing, the launcher creates it from `.env.example`. Get a key
using the [shared instructions above](#get-a-gemini-api-key). Once Python
dependencies are installed, a missing, blank, or placeholder key triggers setup
instructions and an offer to save your key using a hidden terminal prompt. Only
`GEMINI_API_KEY` is updated; other settings and comments are preserved. The file
is restricted to read/write access for your user. Existing usable keys are kept.
Validation does not make a Gemini request or verify access/quota. `DATABASE_URL`
is optional because sessions currently use in-memory storage. An invalid key
overriding `.env` must be corrected or unset before setup can continue.
`--yes` accepts dependency installation only; key entry still needs an interactive
terminal and separate consent. Noninteractive runs must supply a key beforehand.

The launcher requires `PORT=8000` and `JOURNAL_DIR=./journals` (or their defaults)
because the frontend currently uses those locations. Ports 8000 and 5173 must
be free; it does not stop existing applications. It installs locked project
dependencies, starts both servers, waits for each to respond, and prints
<http://localhost:5173>. Logs appear in the terminal. Keep the terminal open;
Ctrl+C stops both servers and their children. If either server exits, the other
is stopped too. Each server has a 60-second readiness timeout. This starts the
existing development backend, which listens on all network interfaces.

```bash
./startLinux.sh --check  # Read-only: tools, .env, project environment and ports
./startLinux.sh --yes    # Accept dependency installation without prompting
./startLinux.sh --help
```

`--check` never installs dependencies, creates `.env`, or starts servers. It
requires a previously installed project Python environment for dotenv validation.
Noninteractive runs report missing dependencies and exit unless `--yes` is
specified. Normal runs sync Python dependencies with `uv sync --locked` and use
`npm ci --legacy-peer-deps` when the frontend lockfile changes or dependencies
are missing. Installation failures stop startup and can be retried.

#### Manual setup

Follow these steps after [getting your API key](#get-a-gemini-api-key).

```bash
# Clone the repo
git clone https://github.com/jas1thi/wellbeing-agent.git
cd wellbeing-agent

# Set up environment
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY

# Install Python dependencies
uv sync

# Install frontend dependencies
cd journal-app && npm install --legacy-peer-deps && cd ..
```

### Running

You need two terminals:

```bash
# Terminal 1 — Backend (agent + API)
uv run python main.py
```

```bash
# Terminal 2 — Frontend
cd journal-app && npm run dev
```

Open [http://localhost:5173](http://localhost:5173)

The backend runs on port 8000 (agent chat + search API), and the frontend on port 5173 with Vite proxying API requests.

### Docker

```bash
docker build -t wellbeing-agent .
docker run -p 8000:8000 --env-file .env wellbeing-agent
```

## How it works

1. **Chat** — You talk to the agent through the Check-in page. It's a Gemini-powered conversational agent built with Google ADK that asks about your day.

2. **Journal** — After 2-3 exchanges, the agent calls `save_journal` to create a markdown file with your mood, highlights, and reflections. It also calls Gemini's image generation to create a cartoon.

3. **Search** — When you search, your query gets embedded with the same sentence-transformer model used to index all journal chunks. FAISS finds the closest matches by cosine similarity.

4. **Frontend** — The React app reads journal files through a Vite dev server plugin (dev) or FastAPI static routes (production). No database needed for the journal content — it's all markdown files and PNG images.

## Project structure

```
wellbeing-agent/
├── main.py                    # Entry point — FastAPI + ADK
├── wellbeing_agent/
│   ├── agent.py               # Agent definition + prompt
│   ├── api.py                 # REST API routes
│   └── tools/
│       ├── journal_store.py   # Save/read/search journals (FAISS)
│       └── image_gen.py       # Gemini cartoon generation
├── journal-app/               # React frontend
│   ├── src/
│   │   ├── pages/             # Dashboard, Entries, Journal, Calendar, Chat
│   │   ├── components/        # Sidebar, SearchModal, SharePreview, etc.
│   │   ├── hooks/             # useJournals, useStats, useSearch
│   │   └── lib/               # API client, share card generator
│   └── vite.config.js         # Dev server + API plugin
├── journals/                  # Markdown entries + cartoon PNGs
└── Dockerfile
```

## License

Do whatever you want with it. If you build something cool on top of this, I'd love to hear about it.
