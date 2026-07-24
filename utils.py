import os, json, time, discord, aiohttp
from database import db_get, db_set, logger, GEMINI_KEY

COMANDOS_TMP_FILE = "comandos_hora.tmp"
from collections import defaultdict
_request_times = defaultdict(list)

MODELOS_GEMINI = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash", "gemini-3.1-flash-lite", "gemini-3.1-pro", "gemini-3-pro", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2-flash", "gemini-2-pro", "gemini-1.5-pro", "gemini-1.5-flash"]

def check_rate_limit(ip: str) -> bool:
    now = time.time()
    _request_times[ip] = [t for t in _request_times[ip] if now - t < 60]
    if len(_request_times[ip]) >= 30: return False
    _request_times[ip].append(now)
    return True

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

def analisar_texto(texto: str) -> dict:
    return {"totais": len(texto), "sem_espaco": len("".join(texto.split())), "linhas": len(texto.splitlines()), "palavras": len(texto.split()), "espacos": texto.count(" ")}

def corrigir_texto_sem_ia(texto: str) -> str:
    correcoes = {"apos": "após", "nao": "não", "vc": "você", "tmb": "também", "pq": "porque", "oque": "o que", "concerteza": "com certeza", "excessao": "exceção", "derrepente": "de repente"}
    sinonimos = {"fazer": "realizar", "bom": "excelente", "grande": "imenso", "feliz": "alegre", "triste": "chateado", "falar": "dizer", "coisa": "elemento", "muito": "bastante"}
    palavras = texto.split(" ")
    resultado = []; recentes = []
    for p in palavras:
        prefixo = ""; sufixo = ""
        while p and p[0] in "*\"'>.,!?": prefixo += p[0]; p = p[1:]
        while p and p[-1] in "*\"'>.,!?": sufixo = p[-1] + sufixo; p = p[:-1]
        clean_p = p.lower()
        if clean_p in correcoes:
            p = correcoes[clean_p]
            clean_p = p.lower()
        if clean_p in recentes and clean_p in sinonimos:
            p = sinonimos[clean_p]
        resultado.append(f"{prefixo}{p}{sufixo}")
        recentes.append(clean_p)
        if len(recentes) > 10: recentes.pop(0)
    return " ".join(resultado)

async def chamar_gemini(system_prompt: str, user_prompt: str, start_idx: int = 0):
    if not GEMINI_KEY: return "❌ Chave da API (Key) não configurada no ambiente.", start_idx
    async with aiohttp.ClientSession() as session:
        for i in range(start_idx, len(MODELOS_GEMINI)):
            modelo = MODELOS_GEMINI[i]
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_KEY}"
            payload = {"system_instruction": {"parts": [{"text": system_prompt}]}, "contents": [{"parts": [{"text": user_prompt}]}], "generationConfig": {"temperature": 0.7}}
            try:
                async with session.post(url, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["candidates"][0]["content"]["parts"][0]["text"], i
            except Exception: continue
    return "❌ Todos os modelos de IA falharam. Tente novamente mais tarde.", len(MODELOS_GEMINI)