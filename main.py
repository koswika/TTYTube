import os
import sys
import re
import time
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("YOUTUBE_API_KEY")

if not API_KEY:
    print("ERROR: YOUTUBE_API_KEY not found in .env file")
    sys.exit(1)

POLL_INTERVAL = 3
MAX_RESULTS = 100
STATE_FILE = ".yt_chat_state.json"

def extract_video_id(url_or_id):
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:[?&]|$)",
        r"youtu\.be\/([0-9A-Za-z_-]{11})",
        r"live\/([0-9A-Za-z_-]{11})"
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    if re.match(r"^[0-9A-Za-z_-]{11}$", url_or_id):
        return url_or_id
    return None

def remove_emoji(text):
    emoji_pattern = re.compile(
        "["
        u"\U0001F600-\U0001F64F"
        u"\U0001F300-\U0001F5FF"
        u"\U0001F680-\U0001F6FF"
        u"\U0001F1E0-\U0001F1FF"
        u"\U00002702-\U000027B0"
        u"\U000024C2-\U0001F251"
        u"\U0001F900-\U0001F9FF"
        u"\U0001FA70-\U0001FAFF"
        u"\U00002600-\U000026FF"
        u"\U00002700-\U0000277F"
        u"\U0001F700-\U0001F77F"
        u"\U0001F780-\U0001F7FF"
        u"\U0001F800-\U0001F8FF"
        u"\U0001F980-\U0001F9FF"
        u"\U0001FA00-\U0001FA6F"
        "]+",
        flags=re.UNICODE
    )
    return emoji_pattern.sub(r'', text)

def check_and_get_live_chat_id(video_id):
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "liveStreamingDetails",
        "id": video_id,
        "key": API_KEY
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        return False, f"API error: {e}"
    items = data.get("items", [])
    if not items:
        return False, "Video not found."
    details = items[0].get("liveStreamingDetails", {})
    if "activeLiveChatId" in details:
        return True, details["activeLiveChatId"]
    else:
        return False, "Not currently live, chat disabled, or age-restricted."

def fetch_messages(live_chat_id, page_token=None):
    url = "https://www.googleapis.com/youtube/v3/liveChat/messages"
    params = {
        "liveChatId": live_chat_id,
        "part": "snippet,authorDetails",
        "key": API_KEY,
        "maxResults": MAX_RESULTS
    }
    if page_token:
        params["pageToken"] = page_token
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e)}

def format_message(item, show_time=True):
    author = item.get("authorDetails", {}).get("displayName", "Unknown")
    snippet = item.get("snippet", {})
    message = snippet.get("displayMessage")

    if not message:
        if "superChatDetails" in snippet:
            message = "Super Chat: " + snippet["superChatDetails"].get("userComment", "")
        elif "superStickerDetails" in snippet:
            message = "Super Sticker"
        elif "membershipDetails" in snippet:
            message = "Membership: " + snippet["membershipDetails"].get("memberMessage", "")
        else:
            message = "[System message]"

    author = remove_emoji(author)
    message = remove_emoji(message)

    if len(author) > 18:
        author = author[:15] + "..."
    if show_time:
        ts = datetime.now().strftime("%H:%M:%S")
        return f"[{ts}] {author}: {message}"
    return f"{author}: {message}"

def load_state(video_id):
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                if data.get("video_id") == video_id:
                    return data.get("page_token"), set(data.get("seen_ids", []))
        except:
            pass
    return None, set()

def save_state(video_id, page_token, seen_ids):
    if len(seen_ids) > 2000:
        seen_ids = set(sorted(seen_ids)[-2000:])
    with open(STATE_FILE, 'w') as f:
        json.dump({"video_id": video_id, "page_token": page_token, "seen_ids": list(seen_ids)}, f)

def main():
    if len(sys.argv) < 2:
        print("Usage: python live_yt_chat.py <YouTube_URL_or_Video_ID>")
        sys.exit(1)

    video_input = sys.argv[1]
    video_id = extract_video_id(video_input)

    if not video_id:
        print("ERROR: Could not extract a valid Video ID.")
        sys.exit(1)

    print(f"Checking video: {video_id}")

    is_live, result = check_and_get_live_chat_id(video_id)

    if not is_live:
        print(f"ERROR: {result}")
        sys.exit(1)

    live_chat_id = result
    print(f"Connected to live chat (ID: {live_chat_id})")
    print(f"Polling every {POLL_INTERVAL}s (press Ctrl+C to exit)\n")

    page_token, seen_ids = load_state(video_id)
    total_printed = len(seen_ids)
    spinner = ['|', '/', '-', '\\']
    spin_idx = 0
    status_line = ""
    first_fetch = True

    try:
        while True:
            data = fetch_messages(live_chat_id, page_token)

            if "error" in data:
                print(f"\nERROR: {data['error']}")
                time.sleep(POLL_INTERVAL)
                continue

            new_count = 0
            if "items" in data and data["items"]:
                for item in data["items"]:
                    msg_id = item["id"]
                    if msg_id not in seen_ids:
                        seen_ids.add(msg_id)
                        print(format_message(item, show_time=True))
                        new_count += 1
                        total_printed += 1
                        if first_fetch:
                            first_fetch = False

            if "nextPageToken" in data:
                page_token = data["nextPageToken"]
                save_state(video_id, page_token, seen_ids)
            elif first_fetch:
                # If first fetch returns no nextPageToken, wait and retry
                pass

            spin = spinner[spin_idx % len(spinner)]
            spin_idx += 1
            now = datetime.now().strftime("%H:%M:%S")
            status = f"{spin} [{now}] total={total_printed} new={new_count}  "
            if new_count == 0:
                status += "(waiting...)"
            if page_token:
                status += f" token={page_token[:8]}..."

            if status_line:
                sys.stdout.write("\r" + " " * len(status_line) + "\r")
            sys.stdout.write(status)
            sys.stdout.flush()
            status_line = status

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n\nDisconnected. Goodbye!")

if __name__ == "__main__":
    main()