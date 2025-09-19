from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle
import os.path
import base64
import email
from bs4 import BeautifulSoup

from langchain_core.messages import SystemMessage

import os
from langchain.chat_models import init_chat_model

from langgraph.graph import START, END, StateGraph, MessagesState
from langgraph.prebuilt import tools_condition, ToolNode

import PyPDF2
from io import BytesIO

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']


def getEmailsBySubject(subject_keywords, max_results=10) -> str:
    """
    Get emails by subject keywords
    Args:
        subject_keywords: str
        max_results: int
    Returns:
        str: emails text
    """
    # Variable creds will store the user access token.
    # If no valid token found, we will create one.
    creds = None

    # The file token.pickle contains the user access token.
    # Check if it exists
    if os.path.exists('token.pickle'):

        # Read the token from the file and store it in the variable creds
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)

    # If credentials are not available or are invalid, ask the user to log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Token refresh failed: {e}")
                print("Re-authenticating...")
                creds = None  # Reset creds to force re-authentication
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)

        # Save the access token in token.pickle file for the next run
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    # Connect to the Gmail API
    service = build('gmail', 'v1', credentials=creds)

    query = f'"{subject_keywords}"'  # Basic search in the entire email

    emails_text = ""

    try:
        # Použijeme query parametr pro vyhledávání
        result = service.users().messages().list(
            userId='me', 
            q=query,
            maxResults=max_results
        ).execute()

        messages = result.get('messages', [])
        
        if not messages:
            print(f"Nenalezeny žádné emaily pro téma: {subject_keywords}")
            return []
        
        print(f"Nalezeno {len(messages)} emailů pro téma: {subject_keywords}")

        # iterate through all the messages
        for msg in messages:
            # Get the message from its id
            txt = service.users().messages().get(userId='me', id=msg['id']).execute()

            # Use try-except to avoid any Errors
            try:
                # Get value of 'payload' from dictionary 'txt'
                payload = txt['payload']
                headers = payload['headers']

                subject = sender = ""

                for d in headers:
                        if d['name'] == 'Subject':
                            subject = d['value']
                        if d['name'] == 'From':
                            sender = d['value']
                # Look for Subject and Sender Email in the headers
                body_data = None

                def walk_parts(parts):
                    nonlocal body_data, emails_text
                    for part in parts:
                        mime_type = part.get('mimeType')
                        filename = part.get('filename') or ""
                        body = part.get('body', {})
                        data_inline = body.get('data')
                        attachment_id = body.get('attachmentId')

                        # Capture inline body (prefer first text/plain or text/html)
                        if body_data is None and mime_type in ['text/plain', 'text/html'] and data_inline:
                            body_data = data_inline

                        # If this is an attachment, fetch and include readable text-based content
                        if filename and attachment_id:
                            try:
                                attachment = service.users().messages().attachments().get(
                                    userId='me', messageId=txt['id'], id=attachment_id
                                ).execute()
                                attach_data = attachment.get('data')
                                attachment_text = ""
                                if attach_data:
                                    attach_data = attach_data.replace("-", "+").replace("_", "/")
                                    raw_bytes = base64.b64decode(attach_data)
                                    
                                    # Check if it's a PDF file
                                    if filename.lower().endswith('.pdf') or mime_type == 'application/pdf':
                                        try:
                                            # Read PDF content
                                            pdf_reader = PyPDF2.PdfReader(BytesIO(raw_bytes))
                                            pdf_text = ""
                                            for page_num in range(len(pdf_reader.pages)):
                                                page = pdf_reader.pages[page_num]
                                                pdf_text += page.extract_text() + "\n"
                                            attachment_text = f"PDF Content:\n{pdf_text.strip()}"
                                        except Exception as e:
                                            attachment_text = f"(PDF parsing error: {e})"
                                    
                                    # Check if it's other text-based attachments
                                    elif (
                                        (mime_type and mime_type.startswith('text/')) or
                                        filename.lower().endswith(('.txt', '.csv', '.json', '.md', '.log'))
                                    ):
                                        try:
                                            attachment_text = raw_bytes.decode('utf-8', errors='replace')
                                        except Exception:
                                            attachment_text = "(unable to decode as text)"
                                    
                                    # For other binary files, just note the info
                                    else:
                                        attachment_text = f"(binary attachment, {len(raw_bytes)} bytes)"

                                emails_text += f"\nAttachment: {filename} ({mime_type})\n{attachment_text}\n"
                            except Exception as e:
                                emails_text += f"\nAttachment: {filename} (error fetching attachment: {e})\n"

                        # Walk recursively for nested multiparts
                        if part.get('parts'):
                            walk_parts(part['parts'])

                if 'parts' in payload:
                    walk_parts(payload['parts'])
                else:
                    body_data = payload.get('body', {}).get('data')

                if body_data:
                    body_data = body_data.replace("-", "+").replace("_", "/")
                    decoded_data = base64.b64decode(body_data).decode("utf-8")

                    soup = BeautifulSoup(decoded_data, "html.parser")
                    body = soup.get_text()
                else:
                    body = "(no content)"

                email_text = f"Subject: {subject}\nFrom: {sender}\nMessage:\n{body}\n"
                emails_text += email_text
            except Exception as e:
                print("Error: ", e)
    except Exception as e:
        print(f"Error: {e}")
        return ""
    return emails_text
        
tools = [getEmailsBySubject]

# getEmailsBySubject("ceskaposta", 3)
# Define LLM with bound tools
# Load API key from environment variable
google_api_key = os.getenv("GOOGLE_API_KEY")
if not google_api_key:
    raise ValueError("GOOGLE_API_KEY environment variable is not set")
os.environ["GOOGLE_API_KEY"] = google_api_key
llm = init_chat_model("google_genai:gemini-2.0-flash")
llm_with_tools = llm.bind_tools(tools)

# System message
sys_msg = SystemMessage(content="You are a helpful assistant and you will be given subject keywods - you are tasked with reading all emails with given subject and suggesting a response to the last email in the newest thread about given subject.")

# Node
def assistant(state: MessagesState):
   return {"messages": [llm_with_tools.invoke([sys_msg] + state["messages"])]}

# Build graph
builder = StateGraph(MessagesState)
builder.add_node("assistant", assistant)
builder.add_node("tools", ToolNode(tools))
builder.add_edge(START, "assistant")
builder.add_conditional_edges(
    "assistant",
    # If the latest message (result) from assistant is a tool call -> tools_condition routes to tools
    # If the latest message (result) from assistant is a not a tool call -> tools_condition routes to END
    tools_condition,
)
builder.add_edge("tools", "assistant")
builder.add_edge("assistant", END)

# Compile graph
graph = builder.compile()