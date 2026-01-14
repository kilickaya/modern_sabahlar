import json
import os
import re
import tempfile
import urllib.parse

import requests
import streamlit as st

st.set_page_config(page_title="Modern Sabahlar Player", layout="wide")

DEFAULT_SHARE = "4RaM/vXuYxiCgD"

CLOUD_PUBLIC = "https://cloud.mail.ru/public"
API_FOLDER = "https://cloud.mail.ru/api/v2/folder"
API_TOKEN = "https://cloud.mail.ru/api/v2/tokens/download"

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}

PROGRESS_PATH = "progress.json"
PAGE_SIZE = 200

def safe_quote(s: str) -> str:
    return urllib.parse.quote(s, safe="~@#$()*!=:;,.?/\\'")

def read_progress() -> dict:
    try:
        with open(PROGRESS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def write_progress(d: dict) -> None:
    tmp_dir = os.path.dirname(PROGRESS_PATH) or "."
    os.makedirs(tmp_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="progress_", suffix=".json", dir=tmp_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, PROGRESS_PATH)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

@st.cache_data(ttl=60 * 5, show_spinner=False)
def get_download_token_cached() -> str:
    r = requests.get(API_TOKEN, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data["body"]["token"]

def get_download_token() -> str:
    if "token" not in st.session_state or "token_ts" not in st.session_state:
        st.session_state.token = get_download_token_cached()
        st.session_state.token_ts = st.session_state.get("now_ts", 0)
        return st.session_state.token
    age = st.session_state.get("now_ts", 0) - st.session_state.token_ts
    if age > 60 * 4:
        st.session_state.token = get_download_token_cached()
        st.session_state.token_ts = st.session_state.get("now_ts", 0)
    return st.session_state.token

@st.cache_data(ttl=60 * 30, show_spinner=False)
def get_base_url(share: str) -> str:
    r = requests.get("https://cloud.mail.ru/api/v2/dispatcher", headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    body = data.get("body", {}) or {}
    weblink_get = body.get("weblink_get") or body.get("weblink_get_url") or None
    if isinstance(weblink_get, list) and weblink_get and isinstance(weblink_get[0], dict) and "url" in weblink_get[0]:
        return weblink_get[0]["url"]
    if isinstance(weblink_get, str) and weblink_get:
        return weblink_get
    raise RuntimeError("Dispatcher did not return body.weblink_get[0].url")


def list_dir(share: str, offset: int, limit: int) -> dict:
    params = {"weblink": share.strip("/"), "offset": offset, "limit": limit, "api": 2}
    r = requests.get(API_FOLDER, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

def normalize_child_weblink(parent: str, item: dict) -> str:
    w = item.get("weblink", "") or ""
    name = item.get("name", "") or ""
    if not w:
        return ""
    if name and not w.endswith("/" + name):
        w = w.rstrip("/") + "/" + name
    return w.strip("/")

def build_file_url(base_url: str, token: str, file_weblink: str) -> str:
    return f"{base_url}/{safe_quote(file_weblink.strip('/'))}?key={token}"

def audio_html(src: str) -> str:
    esc = src.replace('"', "%22")
    return f"""
    <audio controls preload="none" style="width: 100%;">
      <source src="{esc}" type="audio/mpeg">
    </audio>
    """

date_re = re.compile(r"Modern[_ ]Sabahlar[_ ](\d{2})_(\d{2})_(\d{2})\.mp3$", re.IGNORECASE)

def parse_date_key(name: str):
    m = date_re.search(name.strip())
    if not m:
        return None
    d, mo, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    year = 2000 + yy if yy < 70 else 1900 + yy
    return (year, mo, d)

def file_sort_key(item: dict):
    name = (item.get("name", "") or "").strip()
    dk = parse_date_key(name)
    if dk is None:
        return (1, name.lower())
    return (0, dk, name.lower())

def folder_sort_key(item: dict):
    return (item.get("name", "") or "").lower()

def get_counts(body: dict) -> tuple[int, int]:
    c = body.get("count", {}) or {}
    return int(c.get("folders", 0)), int(c.get("files", 0))

def update_progress(folder: str, filename: str):
    p = read_progress()
    p["last_folder"] = folder
    p["last_file"] = filename
    pf = p.get("per_folder", {}) or {}
    pf[folder] = filename
    p["per_folder"] = pf
    write_progress(p)

def pick_last_for_folder(folder: str, names: list[str]) -> str | None:
    p = read_progress()
    pf = p.get("per_folder", {}) or {}
    cand = pf.get(folder)
    if cand in names:
        return cand
    if p.get("last_folder") == folder and p.get("last_file") in names:
        return p.get("last_file")
    return None

st.session_state.now_ts = st.session_state.get("now_ts", 0) + 1

st.sidebar.title("Modern Sabahlar Player")
share = st.sidebar.text_input("Public share path", value=DEFAULT_SHARE).strip().strip("/")

if not share:
    st.stop()

if "last_share" not in st.session_state:
    st.session_state.last_share = share

if share != st.session_state.last_share:
    st.session_state.last_share = share
    st.session_state.nav = [share]
    st.session_state.offset = 0
    st.session_state.loaded_files = []
    st.session_state.loaded_folders = []
    st.session_state.selected_name = None

if "nav" not in st.session_state:
    st.session_state.nav = [share]

if "offset" not in st.session_state:
    st.session_state.offset = 0

if "loaded_files" not in st.session_state:
    st.session_state.loaded_files = []

if "loaded_folders" not in st.session_state:
    st.session_state.loaded_folders = []

current = st.session_state.nav[-1]

with st.sidebar:
    if st.button("Reset to root"):
        st.session_state.nav = [share]
        st.session_state.offset = 0
        st.session_state.loaded_files = []
        st.session_state.loaded_folders = []
        st.session_state.selected_name = None
        st.rerun()

    if len(st.session_state.nav) > 1:
        if st.button("⬅️ Up one level"):
            st.session_state.nav.pop()
            st.session_state.offset = 0
            st.session_state.loaded_files = []
            st.session_state.loaded_folders = []
            st.session_state.selected_name = None
            st.rerun()

    st.caption("Path:")
    for p in st.session_state.nav:
        st.write("• " + p)

colA, colB, colC = st.columns([2, 1, 1], vertical_alignment="center")
with colA:
    st.title("Modern Sabahlar Archive")
    st.caption(f"Current: {current}")
with colB:
    if st.button("Refresh now"):
        st.cache_data.clear()
        st.session_state.offset = 0
        st.session_state.loaded_files = []
        st.session_state.loaded_folders = []
        st.session_state.selected_name = None
        st.rerun()
with colC:
    if st.button("Refresh token"):
        st.session_state.pop("token", None)
        st.session_state.pop("token_ts", None)
        st.rerun()

try:
    base_url = get_base_url(share)
except Exception as e:
    st.error(f"Failed to initialize base URL. Error: {e}")
    st.stop()

token = get_download_token()

def load_first_page_if_needed():
    if st.session_state.offset > 0 or st.session_state.loaded_files or st.session_state.loaded_folders:
        return
    obj = list_dir(current, offset=0, limit=PAGE_SIZE)
    body = obj.get("body", {}) or {}
    items = body.get("list", []) or []
    folders = [it for it in items if it.get("type") == "folder"]
    files = [it for it in items if it.get("type") == "file"]
    st.session_state.loaded_folders = sorted(folders, key=folder_sort_key)
    st.session_state.loaded_files = sorted(files, key=file_sort_key)
    st.session_state.offset = len(items)

def load_more():
    obj = list_dir(current, offset=st.session_state.offset, limit=PAGE_SIZE)
    body = obj.get("body", {}) or {}
    items = body.get("list", []) or []
    folders = [it for it in items if it.get("type") == "folder"]
    files = [it for it in items if it.get("type") == "file"]
    st.session_state.loaded_folders = sorted(st.session_state.loaded_folders + folders, key=folder_sort_key)
    st.session_state.loaded_files = sorted(st.session_state.loaded_files + files, key=file_sort_key)
    st.session_state.offset += len(items)
    return body

try:
    load_first_page_if_needed()
    body0 = list_dir(current, offset=0, limit=1).get("body", {}) or {}
    n_folders_total, n_files_total = get_counts(body0)
except Exception as e:
    st.error(f"Failed to list folder. Error: {e}")
    st.stop()

folders = st.session_state.loaded_folders
files = st.session_state.loaded_files

st.subheader("Folders")
if not folders and n_folders_total == 0:
    st.info("No subfolders found here.")
else:
    folder_names = [f.get("name", "(unnamed)") for f in folders]
    chosen = st.selectbox("Open folder", ["(select)"] + folder_names, index=0)
    if chosen != "(select)":
        idx = folder_names.index(chosen)
        child = normalize_child_weblink(current, folders[idx])
        if child:
            st.session_state.nav.append(child)
            st.session_state.offset = 0
            st.session_state.loaded_files = []
            st.session_state.loaded_folders = []
            st.session_state.selected_name = None
            st.rerun()

st.subheader("Audio files")
q = st.text_input("Search in loaded files (filename contains)").strip().lower()

filtered = []
for f in files:
    name = f.get("name", "") or ""
    if not q or q in name.lower():
        filtered.append(f)

st.write(f"Loaded files: {len(files)} / {n_files_total} | Matching: {len(filtered)}")

more_possible = len(files) < n_files_total
if more_possible:
    if st.button("Load more files"):
        try:
            load_more()
        except Exception as e:
            st.error(f"Failed to load more. Error: {e}")
        st.rerun()

if not filtered:
    st.info("No matching MP3 files in the loaded set.")
    st.stop()

names = [f.get("name", "(unnamed)") for f in filtered]

preferred = pick_last_for_folder(current, names)
default_index = 0
if preferred is not None:
    default_index = names.index(preferred)
elif st.session_state.get("selected_name") in names:
    default_index = names.index(st.session_state.selected_name)

selected_name = st.selectbox("Select an episode", names, index=default_index)
st.session_state.selected_name = selected_name

sel_idx = names.index(selected_name)
sel_item = filtered[sel_idx]
sel_weblink = normalize_child_weblink(current, sel_item)
src = build_file_url(base_url, token, sel_weblink)

update_progress(current, selected_name)

col1, col2, col3 = st.columns([1, 1, 3], vertical_alignment="center")
with col1:
    st.link_button("Open direct stream", src)
with col2:
    if st.button("Next ▶️"):
        nxt = min(sel_idx + 1, len(names) - 1)
        st.session_state.selected_name = names[nxt]
        st.rerun()
with col3:
    st.caption("iOS Safari blocks autoplay on rerun. Tap Play in the player.")

st.components.v1.html(audio_html(src), height=70)

meta_cols = st.columns(4)
size = sel_item.get("size", None)
mtime = sel_item.get("mtime", None)
with meta_cols[0]:
    st.write("**Name**")
    st.write(selected_name)
with meta_cols[1]:
    st.write("**Size**")
    st.write(f"{(size / (1024*1024)):.1f} MB" if isinstance(size, (int, float)) else "Unknown")
with meta_cols[2]:
    st.write("**Modified**")
    st.write(mtime if mtime else "Unknown")
with meta_cols[3]:
    st.write("**Folder**")
    st.write(current)
