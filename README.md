# ContentOS Backend

A Fast API based content generation API that transforms YouTube transcripts into platform-optimized content using advanced AI and intelligent agent orchestration.

## ✨ What It Does

ContentOS Backend powers intelligent content repurposing:

- **🎬 Multi-Platform Content** — Generate LinkedIn posts, Twitter threads, TikTok scripts, Instagram captions, blog posts, and more from a single source
- **🧠 AI-Powered** — Uses Groq's advanced LLM with intelligent agent coordination
- **💾 Smart Caching** — Optimized transcript caching for faster generation
- **👤 Voice Profiling** — Extract and maintain consistent creator voice across all platforms
- **⚡ Async Processing** — Long-running jobs with real-time progress tracking
- **🔐 Enterprise Auth** — JWT-based authentication with secure session management
- **🗄️ Persistent Storage** — PostgreSQL-backed data persistence

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- PostgreSQL database
- Groq API key

### Installation

```bash
# Clone and navigate
cd backend

# Create virtual environment
python -m venv venv

# Activate (macOS/Linux)
source venv/bin/activate
# Or Windows
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials
```

### Run Locally

```bash
python -m uvicorn app.main:app --reload --port 8000
```

Access the API at `http://localhost:8000` and interactive docs at `http://localhost:8000/docs`

## 🔧 Environment Setup

Create a `.env` file:

```env
DATABASE_URL=postgresql://user:password@localhost/contentos
GROQ_API_KEY=your_groq_api_key_here
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000
```

### Optional Services

```env
TRANSCRIPTYT_API_KEY=your_transcript_api_key
YTDLP_USER_AGENT=Mozilla/5.0...
```

## 📡 Core API Endpoints

### Authentication
```
POST   /auth/register         Create new account
POST   /auth/login            Authenticate user
GET    /me                    Get current user
```

### Content Generation
```
POST   /generation-jobs       Start content generation
GET    /generation-jobs/{id}  Get job status & results
GET    /target-assets         List supported platforms
```

### Creator Profile
```
GET    /me/voice-profile                    Get voice profile
POST   /me/voice-profile                    Create from writing samples
POST   /me/voice-profile/from-youtube       Extract from YouTube videos
```

### Integrations (Ready for Extension)
```
GET    /integrations                        List integrations
POST   /integrations/{platform}/connect     Connect platform
DELETE /integrations/{platform}             Disconnect platform
```

## 🎯 Supported Platforms

Generate optimized content for:

- **LinkedIn** — Professional posts and articles
- **Twitter/X** — Thread formats and quick takes
- **TikTok** — Short-form video scripts
- **Instagram** — Carousel posts and captions
- **Reddit** — Community-appropriate posts
- **Medium** — Blog-style articles
- **Substack** — Newsletter content
- **Email** — Newsletter formatting


## 💾 Database

Powered by PostgreSQL with optimized schema for:

- User authentication and authorization
- Voice profile storage (JSONB for flexibility)
- Transcript caching with TTL
- Generation job tracking
- User content association

## 🛠️ Tech Stack

- **Framework** — FastAPI (modern, fast, production-ready)
- **Server** — Uvicorn with async support
- **Database** — PostgreSQL with SQLAlchemy ORM
- **AI/LLM** — Groq API for high-performance inference
- **Auth** — JWT + bcrypt
- **Video Processing** — yt-dlp with fallback support

## 📊 Performance Features

- ✅ Async/await throughout for high concurrency
- ✅ Connection pooling for database efficiency
- ✅ Transcript caching to avoid redundant fetches
- ✅ Groq's fast inference (120B parameter model)
- ✅ Optimized agent coordination
- ✅ Real-time progress tracking

## 🔐 Security

- JWT-based stateless authentication
- Bcrypt password hashing
- CORS protection
- Environment-based configuration
- No sensitive data in logs
- User-scoped data isolation


## 🚀 Deployment

Ready to deploy on:

- **Heroku** — Platform-agnostic
- **Railway** — Modern deployment
- **Render** — Serverless-friendly
- **AWS** — Lambda, EC2, RDS
- **Docker** — Container-ready with nixpacks

### Production Build

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## 📚 API Documentation

Full interactive documentation available:

- **Swagger UI** — `http://localhost:8000/docs`
- **ReDoc** — `http://localhost:8000/redoc`

## 🔌 Integration Points

### Input Sources
- Direct YouTube URLs
- Video IDs
- Pasted transcripts
- Multiple transcript APIs


## ⚙️ Configuration

### LLM Model
- Default: `openai/gpt-oss-120b` via Groq
- Configurable per request
- Cost-optimized with high performance

### Transcript Sources (Priority)
1. TranscriptYT API (if configured)
2. yt-dlp with YouTube auth
3. Direct user input

### Database
- Connection pooling
- Automatic schema management
- Migration tracking
- JSONB support for voice profiles

## 🐛 Error Handling

Standard HTTP status codes with helpful messages:

```
200 — Success
201 — Created
400 — Bad Request
401 — Unauthorized
404 — Not Found
500 — Server Error
```
---
