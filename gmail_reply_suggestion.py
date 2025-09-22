import os
import re
import pickle
import base64
import logging
from io import BytesIO
from typing import List, Dict, Optional, Tuple

from dotenv import load_dotenv
from bs4 import BeautifulSoup
import PyPDF2

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

from langchain_core.messages import SystemMessage
from langchain.chat_models import init_chat_model
from langgraph.graph import START, END, StateGraph, MessagesState
from langgraph.prebuilt import tools_condition, ToolNode

# -----------------------------------------------------------------------------
# Bootstrap / Constants / Logging
# -----------------------------------------------------------------------------
load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
CREDENTIALS_PATH = os.getenv("GOOGLE_CLIENT_SECRET", "credentials.json")
TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "token.pickle")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY environment variable is not set")
os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("gmail_reply_suggestion")

# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------
def _safe_b64url_to_bytes(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/")
    # Pad base64 if needed
    missing = len(s) % 4
    if missing:
        s += "=" * (4 - missing)
    return base64.b64decode(s)

# -----------------------------------------------------------------------------
# Gmail Client: Auth + Fetch
# -----------------------------------------------------------------------------
class GmailClient:
    def __init__(self, credentials_path: str = CREDENTIALS_PATH, token_path: str = TOKEN_PATH):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = build('gmail', 'v1', credentials=self._ensure_creds())
        return self._service

    def _ensure_creds(self):
        creds = None
        if os.path.exists(self.token_path):
            with open(self.token_path, 'rb') as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    log.info("Refreshed OAuth token")
                except Exception as e:
                    log.warning(f"Token refresh failed: {e}. Re-authenticating.")
                    creds = None

            if not creds or not creds.valid:
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)

            with open(self.token_path, 'wb') as token:
                pickle.dump(creds, token)
                log.info("Saved OAuth token")

        return creds

    def search_messages(self, query: str, max_results: int = 10) -> List[Dict]:
        result = self.service.users().messages().list(
            userId='me', q=query, maxResults=max_results
        ).execute()
        return result.get('messages', [])

    def get_message(self, msg_id: str) -> Dict:
        return self.service.users().messages().get(userId='me', id=msg_id).execute()

    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> Optional[bytes]:
        try:
            attachment = self.service.users().messages().attachments().get(
                userId='me', messageId=message_id, id=attachment_id
            ).execute()
            data = attachment.get('data')
            return _safe_b64url_to_bytes(data) if data else None
        except Exception as e:
            log.warning(f"Failed to fetch attachment {attachment_id}: {e}")
            return None

# -----------------------------------------------------------------------------
# Email Parsing: Body + Attachments
# -----------------------------------------------------------------------------
class EmailParser:
    TEXT_EXTS = ('.txt', '.csv', '.json', '.md', '.log')

    def parse_headers(self, payload: Dict) -> Tuple[str, str]:
        subject, sender = "", ""
        for h in payload.get('headers', []):
            name = h.get('name')
            value = h.get('value', "")
            if name == 'Subject':
                subject = value
            elif name == 'From':
                sender = value
        return subject, sender

    def extract_body(self, payload: Dict) -> str:
        body_data = None

        def walk(parts: List[Dict]):
            nonlocal body_data
            for part in parts:
                mime = part.get('mimeType', '')
                data_inline = part.get('body', {}).get('data')
                if body_data is None and mime in ['text/plain', 'text/html'] and data_inline:
                    body_data = data_inline
                if part.get('parts'):
                    walk(part['parts'])

        if 'parts' in payload:
            walk(payload['parts'])
        else:
            body_data = payload.get('body', {}).get('data')

        if not body_data:
            return "(no content)"

        try:
            decoded = _safe_b64url_to_bytes(body_data).decode("utf-8", errors="replace")
            soup = BeautifulSoup(decoded, "html.parser")
            text = soup.get_text()
            return re.sub(r'\n{3,}', '\n\n', text).strip()
        except Exception as e:
            log.warning(f"Failed to decode body: {e}")
            return "(unable to decode body)"

    def extract_attachments_summary(self, client: GmailClient, message: Dict) -> str:
        payload = message.get('payload', {})
        message_id = message.get('id', '')
        summaries = []

        def walk(parts: List[Dict]):
            for part in parts:
                mime = part.get('mimeType', '') or ''
                filename = part.get('filename') or ''
                body = part.get('body', {})
                attachment_id = body.get('attachmentId')

                if filename and attachment_id:
                    raw_bytes = client.get_attachment_bytes(message_id, attachment_id)
                    att_text = ""
                    if raw_bytes is not None:
                        if filename.lower().endswith('.pdf') or mime == 'application/pdf':
                            try:
                                pdf = PyPDF2.PdfReader(BytesIO(raw_bytes))
                                pages = [pdf.pages[i].extract_text() or "" for i in range(len(pdf.pages))]
                                att_text = f"PDF Content:\n{'\n'.join(pages).strip()}"
                            except Exception as e:
                                att_text = f"(PDF parsing error: {e})"
                        elif (mime.startswith('text/') or filename.lower().endswith(self.TEXT_EXTS)):
                            try:
                                att_text = raw_bytes.decode('utf-8', errors='replace')
                            except Exception:
                                att_text = "(unable to decode as text)"
                        else:
                            att_text = f"(binary attachment, {len(raw_bytes)} bytes)"
                    else:
                        att_text = "(error fetching attachment)"

                    summaries.append(f"Attachment: {filename} ({mime})\n{att_text}\n")

                if part.get('parts'):
                    walk(part['parts'])

        if 'parts' in payload:
            walk(payload['parts'])

        return "\n".join(summaries).strip()

# -----------------------------------------------------------------------------
# Public Tool Function (kept for LangGraph), delegates to client/parser
# -----------------------------------------------------------------------------
def getEmailsBySubject(subject_keywords: str, max_results: int = 10) -> str:
    """
    Args:
        subject_keywords (str): Keywords to search for in email subjects.
        max_results (int, optional): Maximum number of emails to retrieve. Defaults to 10.

    Returns:
        str: Concatenated plain-text representation of emails and readable attachments
            matching the subject keywords. Each email includes the subject, sender,
            message body, and any readable attachment content.
    """
    client = GmailClient()
    parser = EmailParser()

    query = f'"{subject_keywords}"'
    emails_text = []

    try:
        messages = client.search_messages(query=query, max_results=max_results)
        if not messages:
            log.info(f"No emails found for: {subject_keywords}")
            return ""

        for m in messages:
            full = client.get_message(m['id'])
            payload = full.get('payload', {})
            subject, sender = parser.parse_headers(payload)
            body = parser.extract_body(payload)
            attachments = parser.extract_attachments_summary(client, full)

            block = [
                f"Subject: {subject}",
                f"From: {sender}",
                "Message:",
                body if body else "(no content)",
            ]
            if attachments:
                block.extend(["", attachments])
            emails_text.append("\n".join(block).strip())

    except Exception as e:
        log.error(f"Error collecting emails: {e}")
        return ""

    return "\n\n" + ("\n\n".join(emails_text)) if emails_text else ""

# print(getEmailsBySubject("ceskaposta", 3))

# -----------------------------------------------------------------------------
# LLM + LangGraph wiring (unchanged behavior)
# -----------------------------------------------------------------------------
tools = [getEmailsBySubject]

llm = init_chat_model("google_genai:gemini-2.0-flash")
llm_with_tools = llm.bind_tools(tools)

sys_msg = SystemMessage(
    content=(
        "You are a helpful assistant. You will be given subject keywords. "
        "Read emails with that subject and suggest a response to the last email "
        "in the newest thread about that subject."
    )
)

def assistant(state: MessagesState):
    return {"messages": [llm_with_tools.invoke([sys_msg] + state["messages"])]}

builder = StateGraph(MessagesState)
builder.add_node("assistant", assistant)
builder.add_node("tools", ToolNode(tools))
builder.add_edge(START, "assistant")
builder.add_conditional_edges("assistant", tools_condition)
builder.add_edge("tools", "assistant")
builder.add_edge("assistant", END)
graph = builder.compile()