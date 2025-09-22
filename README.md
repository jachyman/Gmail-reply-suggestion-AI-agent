## Gmail Reply Suggestion (LangGraph, Gmail API, Gemini)

This project reads your Gmail messages for a given subject, extracts readable content (including attachments like PDFs and text files), and passes it to an LLM to suggest a reply. It uses Google Gmail API, LangGraph, and Gemini via LangChain.

### Features
- Fetch Gmail messages by subject keywords
- Extract plain text from HTML bodies
- Parse attachments (PDF and common text formats)
- Tool function `getEmailsBySubject(...)` wired into a LangGraph agent

---

## Prerequisites
- Python 3.10+
- A Google Cloud project with Gmail API enabled
- OAuth client credentials (Desktop) for Gmail API
- A Gemini API key (set as `GOOGLE_API_KEY`)

---

## Quickstart (Windows PowerShell)

```powershell
# 1) Create and activate a virtual environment (optional)
py -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) Install dependencies
pip install -r requirements.txt

# 3) Prepare environment variables (.env recommended)
# Create a file named .env alongside this README with at least:
# GOOGLE_API_KEY=<your_gemini_key>
# Optionally override paths:
# GOOGLE_CLIENT_SECRET=credentials.json
# GMAIL_TOKEN_PATH=token.pickle

# 4) Place your Google OAuth client file
# Save your OAuth client JSON as credentials.json in the project root

# 5) First run will open a browser to authorize Gmail access
# You can import and call the tool function for a quick check:
python - << 'PY'
from gmail_reply_suggestion import getEmailsBySubject
print(getEmailsBySubject("ceskaposta", 3))
PY
```

If you use the LangGraph agent, import `graph` from `gmail_reply_suggestion.py` and invoke it per your application flow.

---

## Environment Variables
These are read via `python-dotenv` from `.env` if present:

- `GOOGLE_API_KEY` (required): Your Gemini API key.
- `GOOGLE_CLIENT_SECRET` (optional): Path to OAuth client JSON (default: `credentials.json`).
- `GMAIL_TOKEN_PATH` (optional): Path to the saved OAuth token (default: `token.pickle`).

Example `.env`:

```dotenv
GOOGLE_API_KEY=your_gemini_api_key_here
GOOGLE_CLIENT_SECRET=credentials.json
GMAIL_TOKEN_PATH=token.pickle
```

---

## How it works (structure)
- `GmailClient`: Handles OAuth, Gmail API calls, and attachment fetching.
- `EmailParser`: Extracts plain text from email bodies and supported attachments.
- `getEmailsBySubject(subject_keywords, max_results)`: Public function used as a tool by the agent; returns a concatenated plain-text representation of relevant emails.
- LangGraph wiring: Binds the tool to the LLM (`google_genai:gemini-2.0-flash`) and compiles a simple agent graph.

---

## Running the agent
You can integrate `graph` into your app or test it interactively:

```python
from gmail_reply_suggestion import graph

# Example of sending a user message that triggers the tool call under the hood
result = graph.invoke({"messages": [{"role": "user", "content": "ceskaposta"}]})
print(result)
```

## Google OAuth notes
- First run will open a browser for consent; a token is stored at `token.pickle`.
- To reset auth, delete `token.pickle` and run again.
- If you leaked `credentials.json` and/or `token.pickle`, delete that OAuth client in Google Cloud Console, create a new one, and re-consent.