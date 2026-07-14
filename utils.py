import os, json, time, requests, discord, random
from database import db_get, db_set, logger

COMANDOS_TMP_FILE = "comandos_hora.tmp"
from collections import defaultdict
_request_times = defaultdict(list)

def check_rate_limit(ip: str) -> bool:
    now = time.time()
    _request_times[ip] = [t for t in _request_times[ip] if now - t < 60]
    if len(_request_times[ip]) >= 30: return False
    _request_times[ip].append(now)
    return True

def rolar_dado_viciado(lados: int) -> int:
    if lados < 2: lados = 6
    chance = random.randint(1, 100)
    if chance <= 75:
        minimo_alto = max(1, int(lados * 0.6))
        return random.randint(minimo_alto, lados)
    return random.randint(1, lados)

def registrar_execucao_comando():
    agora = time.time(); d = {"timestamp_inicial": agora, "quantidade": 0}
    if os.path.exists(COMANDOS_TMP_FILE):
        try:
            with open(COMANDOS_TMP_FILE, "r", encoding="utf-8") as f: d = json.load(f)
        except Exception: pass
    if agora - d.get("timestamp_inicial", agora) > 3600: d = {"timestamp_inicial": agora, "quantidade": 1}
    else: d["quantidade"] = d.get("quantidade", 0) + 1
    try:
        with open(COMANDOS_TMP_FILE, "w", encoding="utf-8") as f: json.dump(d, f)
    except Exception: pass

def obter_metricas_comandos() -> tuple:
    if not os.path.exists(COMANDOS_TMP_FILE): return 0, 0.0
    try:
        with open(COMANDOS_TMP_FILE, "r", encoding="utf-8") as f: d = json.load(f)
        agora = time.time()
        if agora - d.get("timestamp_inicial", agora) > 3600:
            try: os.remove(COMANDOS_TMP_FILE)
            except Exception: pass
            return 0, 0.0
        q = d.get("quantidade", 0); return q, (q / 2.0 if q > 0 else 0.0)
    except Exception: return 0, 0.0

def serializar_permissoes_canal(canal) -> list:
    res = []
    for a, ow in canal.overwrites.items():
        t = "role" if isinstance(a, discord.Role) else "member" if isinstance(a, discord.Member) else None
        if not t: continue
        al, de = ow.pair()
        res.append({"type": t, "id": a.id, "allow": al.value, "deny": de.value})
    return res

def deserializar_permissoes_canal(lista: list, guild, role_map: dict) -> dict:
    res = {}
    for ow in lista:
        alvo = role_map.get(str(ow["id"])) if ow["type"] == "role" else guild.get_member(ow["id"]) if ow["type"] == "member" else None
        if not alvo and ow["type"] == "role" and ow["id"] == guild.id: alvo = guild.default_role
        if alvo: res[alvo] = discord.PermissionOverwrite.from_pair(discord.Permissions(ow["allow"]), discord.Permissions(ow["deny"]))
    return res

def extrair_estrutura_completa_servidor(guild) -> dict:
    roles = [{"id": r.id, "name": r.name, "color": r.color.value, "hoist": r.hoist, "mentionable": r.mentionable, "permissions": r.permissions.value} for r in sorted(guild.roles, key=lambda x: x.position) if not r.is_default()]
    cats = [{"id": c.id, "name": c.name, "position": c.position, "overwrites": serializar_permissoes_canal(c)} for c in guild.categories]
    chans = []
    for ch in guild.channels:
        if isinstance(ch, discord.CategoryChannel): continue
        t = "text" if isinstance(ch, discord.TextChannel) else "voice" if isinstance(ch, discord.VoiceChannel) else None
        if t: chans.append({"id": ch.id, "type": t, "name": ch.name, "category_id": ch.category.id if ch.category else None, "position": ch.position, "topic": getattr(ch, "topic", None), "overwrites": serializar_permissoes_canal(ch)})
    return {"guild_name": guild.name, "roles": roles, "categories": cats, "channels": chans}