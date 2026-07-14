import os, json, requests, logging
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

TOKEN = os.environ.get('DISCORD_TOKEN')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
GIST_ID = os.environ.get('GIST_ID')
DONO_BOT_ID = 1520539744457461892
SENHA_ADMIN_FILE = "senha_admin.txt"

_local_cache = {}

TEMAS_DISPONIVEIS = {
    "dark": {"nome": "🌙 Dark", "fundo": "#1a1a1a", "texto_principal": "#ffffff", "texto_secundario": "#b0b0b0", "caixa_bio": (20, 20, 20, 130)},
    "light": {"nome": "☀️ Light", "fundo": "#f5f5f5", "texto_principal": "#1a1a1a", "texto_secundario": "#4a4a4a", "caixa_bio": (240, 240, 240, 180)},
    "neon": {"nome": "⚡ Neon", "fundo": "#0d0221", "texto_principal": "#ff006e", "texto_secundario": "#8338ec", "caixa_bio": (16, 2, 45, 140)},
    "ocean": {"nome": "🌊 Ocean", "fundo": "#0a1128", "texto_principal": "#00d9ff", "texto_secundario": "#0099cc", "caixa_bio": (5, 20, 60, 130)},
    "sunset": {"nome": "🌅 Sunset", "fundo": "#ff6b35", "texto_principal": "#fff8f0", "texto_secundario": "#ffd700", "caixa_bio": (255, 100, 20, 140)},
    "forest": {"nome": "🌲 Forest", "fundo": "#1b4332", "texto_principal": "#95d5b2", "texto_secundario": "#52b788", "caixa_bio": (20, 40, 25, 130)}
}

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
                logger.info("Gist DB sincronizado!")
                return
            else:
                logger.warning(f"Status {resp.status_code} ao carregar Gist no boot.")
        except Exception as e: logger.error(f"Erro Gist: {e}")
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
    except Exception as e: logger.error(f"Erro local_db: {e}"); return False
    if GITHUB_TOKEN and GIST_ID:
        headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        payload = {"files": {"database.json": {"content": json.dumps(_local_cache, indent=4, ensure_ascii=False)}}}
        try:
            resp = requests.patch(f"https://api.github.com/gists/{GIST_ID}", headers=headers, json=payload, timeout=5)
            return resp.status_code == 200
        except Exception as e: logger.error(f"Erro PATCH Gist: {e}"); return True
    return True

def obter_registro(discord_id: int): return db_get(f"users/{discord_id}")

def obter_ou_auto_registrar(user, guild_id="DM"):
    user_id = str(user.id)
    d = db_get(f"users/{user_id}")
    if d: return d
    bot_id = 0 if user.id == DONO_BOT_ID else db_get("global_config/proximo_bot_id", 1)
    if bot_id > 0: db_set("global_config/proximo_bot_id", bot_id + 1)
    data = {"discord_id": user_id, "guild_id": guild_id, "bot_id": bot_id, "nome": user.display_name, "tema": "dark", "perfil": {"fundo": "#2f3136", "fundo_url": "", "avatar_pos": "se", "descricao": "Use #perfil-config para mudar!"}}
    db_set(f"users/{user_id}", data)
    return data

def obter_tema(user_data: dict, nome_tema=None):
    t = nome_tema or user_data.get("tema", "dark")
    return TEMAS_DISPONIVEIS.get(t, TEMAS_DISPONIVEIS["dark"])

def obter_senha_admin(): return db_get("admin_config/password", "")

def salvar_senha_admin(senha: str):
    try:
        with open(SENHA_ADMIN_FILE, "w", encoding="utf-8") as f: f.write(senha.strip())
    except Exception: pass
    return db_set("admin_config/password", senha.strip())

def existe_senha_admin(): return len(obter_senha_admin()) > 0