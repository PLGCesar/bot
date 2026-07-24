import os, json, requests
import logging

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

TOKEN = os.environ.get('DISCORD_TOKEN')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
GIST_ID = os.environ.get('GIST_ID')
GEMINI_KEY = os.environ.get('Key')
DONO_BOT_ID = 1520539744457461892
SENHA_ADMIN_FILE = "senha_admin.txt"

_local_cache = {}

def sincronizar_banco_local():
    global _local_cache
    if GITHUB_TOKEN and GIST_ID:
        headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        try:
            resp = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=headers, timeout=5)
            if resp.status_code == 200:
                _local_cache = json.loads(resp.json().get("files", {}).get("database.json", {}).get("content", "{}"))
                with open("local_db.json", "w", encoding="utf-8") as f:
                    json.dump(_local_cache, f, indent=4, ensure_ascii=False)
                return
        except Exception: pass
    if os.path.exists("local_db.json"):
        try:
            with open("local_db.json", "r", encoding="utf-8") as f: _local_cache = json.load(f)
        except Exception: _local_cache = {}

def db_get(path: str, default=None):
    temp = _local_cache
    try:
        for k in path.strip("/").split("/"): temp = temp[k]
        return temp
    except Exception: return default

def db_set(path: str, value) -> bool:
    global _local_cache
    keys = path.strip("/").split("/")
    temp = _local_cache
    for k in keys[:-1]:
        if k not in temp or not isinstance(temp[k], dict): temp[k] = {}
        temp = temp[k]
    temp[keys[-1]] = value
    try:
        with open("local_db.json", "w", encoding="utf-8") as f: json.dump(_local_cache, f, indent=4, ensure_ascii=False)
    except Exception: return False
    if GITHUB_TOKEN and GIST_ID:
        headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        payload = {"files": {"database.json": {"content": json.dumps(_local_cache, indent=4, ensure_ascii=False)}}}
        try: requests.patch(f"https://api.github.com/gists/{GIST_ID}", headers=headers, json=payload, timeout=5)
        except Exception: pass
    return True

def obter_senha_admin(): return db_get("admin_config/password", "")

def salvar_senha_admin(senha: str):
    try:
        with open(SENHA_ADMIN_FILE, "w", encoding="utf-8") as f: f.write(senha.strip())
    except Exception: pass
    return db_set("admin_config/password", senha.strip())

def existe_senha_admin(): return len(obter_senha_admin()) > 0