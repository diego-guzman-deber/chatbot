from openai import OpenAI
import shelve
from dotenv import load_dotenv
import os
import time
import logging
import threading

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID")
client = OpenAI(api_key=OPENAI_API_KEY)

_DB_DIR = os.environ.get("DB_PATH", "/app")
_THREADS_DB = os.path.join(_DB_DIR, "threads_db")

# Per-user lock to prevent concurrent webhook retries from racing each other
_user_locks: dict[str, threading.Lock] = {}
_user_locks_meta = threading.Lock()

ACTIVE_RUN_STATUSES = {"queued", "in_progress", "cancelling"}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "expired"}


def get_user_lock(wa_id: str) -> threading.Lock:
    """Return (or create) a per-user lock so that only one request per user
    can be processed at a time."""
    with _user_locks_meta:
        if wa_id not in _user_locks:
            _user_locks[wa_id] = threading.Lock()
        return _user_locks[wa_id]


def upload_file(path):
    # Upload a file with an "assistants" purpose
    file = client.files.create(
        file=open("../../data/airbnb-faq.pdf", "rb"), purpose="assistants"
    )


def create_assistant(file):
    """
    You currently cannot set the temperature for Assistant via the API.
    """
    assistant = client.beta.assistants.create(
        name="WhatsApp AirBnb Assistant",
        instructions="You're a helpful WhatsApp assistant that can assist guests that are staying in our Paris AirBnb. Use your knowledge base to best respond to customer queries. If you don't know the answer, say simply that you cannot help with question and advice to contact the host directly. Be friendly and funny.",
        tools=[{"type": "retrieval"}],
        model="gpt-4-1106-preview",
        file_ids=[file.id],
    )
    return assistant


# Use context manager to ensure the shelf file is closed properly
def check_if_thread_exists(wa_id):
    with shelve.open(_THREADS_DB) as threads_shelf:
        return threads_shelf.get(wa_id, None)


def store_thread(wa_id, thread_id):
    with shelve.open(_THREADS_DB, writeback=True) as threads_shelf:
        threads_shelf[wa_id] = thread_id


def wait_for_active_runs(thread_id, timeout=60):
    """
    Wait until there are no active runs on the thread.
    Raises TimeoutError if it takes longer than `timeout` seconds.
    """
    elapsed = 0
    poll_interval = 1
    while elapsed < timeout:
        runs = client.beta.threads.runs.list(thread_id=thread_id, limit=10)
        active = [r for r in runs.data if r.status in ACTIVE_RUN_STATUSES]
        if not active:
            return  # All clear
        logging.info(
            f"Waiting for {len(active)} active run(s) on thread {thread_id}..."
        )
        time.sleep(poll_interval)
        elapsed += poll_interval
    raise TimeoutError(
        f"Thread {thread_id} still has active runs after {timeout}s. Aborting."
    )


def run_assistant(thread, name):
    # Retrieve the Assistant
    assistant = client.beta.assistants.retrieve(OPENAI_ASSISTANT_ID)

    # Ensure no active runs exist before creating a new one
    wait_for_active_runs(thread.id)

    # Run the assistant
    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=assistant.id,
        # instructions=f"You are having a conversation with {name}",
    )

    # Wait for completion — handle terminal failure states to avoid infinite loop
    while run.status not in TERMINAL_RUN_STATUSES:
        time.sleep(0.5)
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

    if run.status != "completed":
        error_details = getattr(run, "last_error", None)
        logging.error(
            f"Run {run.id} ended with status '{run.status}'. "
            f"last_error: {error_details}"
        )
        raise RuntimeError(
            f"Run {run.id} ended with status '{run.status}'. "
            f"Reason: {error_details}"
        )

    # Retrieve the Messages
    messages = client.beta.threads.messages.list(thread_id=thread.id)
    new_message = messages.data[0].content[0].text.value
    logging.info(f"Generated message: {new_message}")
    return new_message


def generate_response(message_body, wa_id, name):
    lock = get_user_lock(wa_id)

    # Serialize requests per user — if another webhook retry for the same user
    # is already being processed, wait up to 15s then drop the duplicate.
    acquired = lock.acquire(blocking=True, timeout=15)
    if not acquired:
        logging.warning(
            f"Could not acquire lock for {wa_id} within 15s. "
            "Dropping duplicate webhook request."
        )
        return None

    try:
        # Check if there is already a thread_id for the wa_id
        thread_id = check_if_thread_exists(wa_id)

        # If a thread doesn't exist, create one and store it
        if thread_id is None:
            logging.info(f"Creating new thread for {name} with wa_id {wa_id}")
            thread = client.beta.threads.create()
            store_thread(wa_id, thread.id)
            thread_id = thread.id

        # Otherwise, retrieve the existing thread
        else:
            logging.info(f"Retrieving existing thread for {name} with wa_id {wa_id}")
            thread = client.beta.threads.retrieve(thread_id)

        # Wait for any active run to finish before adding a new message
        wait_for_active_runs(thread_id)

        # Add message to thread
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=message_body,
        )

        # Run the assistant and get the new message
        new_message = run_assistant(thread, name)
        return new_message

    finally:
        lock.release()
