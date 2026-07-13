import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask, request, render_template_string, redirect, session, url_for
import os
import json
import threading
import asyncio
import time
import requests
import io
import logging
from collections import defaultdict
from PIL import Image, ImageDraw, ImageFont

# --- CONFIGURAÇÃO DE LOGGING (Console apenas - Render free) ---
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler()]  # Só console, sem arquivo
)
logger = logging.getLogger(__name__)

# --- VARIÁVEIS DE AMBIENTE (Puxadas do Render ou Termux) ---
TOKEN = os.environ.get('DISCORD_TOKEN')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
GIST_ID = os.environ.get('GIST_ID')

# ID do Dono do Bot
DONO_BOT_ID = 1520539744457461892

# --- CONFIGURAÇÃO DO BOT DO DISCORD ---
intents = discord.Intents.default()
intents.message_content = True 
intents.members = True       # IMPORTANTE: Requerido para o on_member_update e moderação ativa

# help_command=None desativa de forma estrita a ajuda padrão de texto do discord.py
bot = commands.Bot(command_prefix="#", intents=intents, help_command=None)

# --- CONFIGURAÇÃO DO SERVIDOR WEB (FLASK) ---
app = Flask(__name__)
SENHA_ADMIN_FILE = "senha_admin.txt"
COMANDOS_TMP_FILE = "comandos_hora.tmp"

# --- RATE LIMITING SIMPLES (prevenção de abuso na web) ---
_request_times = defaultdict(list)
_rate_limit_window = 60  # janela de 60 segundos
_rate_limit_max = 30  # máximo de 30 requisições por janela

def check_rate_limit(ip: str) -> bool:
    """Verifica se um IP excedeu o rate limit. Retorna True se OK, False se bloqueado."""
    now = time.time()
    # Remove requisições antigas (fora da janela)
    _request_times[ip] = [t for t in _request_times[ip] if now - t < _rate_limit_window]
    # Verifica limite
    if len(_request_times[ip]) >= _rate_limit_max:
        return False
    _request_times[ip].append(now)
    return True


# --- ENGENHARIA DE BANCO DE DADOS EM NUVEM (GITHUB GIST DB) ---

# Cache local para evitar requisições desnecessárias (evita rate-limit do GitHub)
_local_cache = {}

def sincronizar_banco_local():
    """Baixa todo o banco de dados do Gist do GitHub e sincroniza com o cache local no startup."""
    global _local_cache
    if GITHUB_TOKEN and GIST_ID:
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        url = f"https://api.github.com/gists/{GIST_ID}"
        try:
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                file_data = data.get("files", {}).get("database.json", {})
                content = file_data.get("content", "{}")
                _local_cache = json.loads(content)
                
                # Salva backup local
                with open("local_db.json", "w", encoding="utf-8") as f:
                    json.dump(_local_cache, f, indent=4, ensure_ascii=False)
                logger.info("Banco de dados em nuvem sincronizado com sucesso!")
                return
            else:
                logger.warning(f"Status {resp.status_code} ao carregar Gist no boot.")
        except requests.exceptions.Timeout:
            logger.error("Timeout ao conectar ao GitHub Gist (5s).")
        except requests.exceptions.ConnectionError:
            logger.error("Erro de conexão ao GitHub Gist.")
        except json.JSONDecodeError:
            logger.error("Erro ao fazer parse do JSON do Gist.")
        except Exception as e:
            logger.error(f"Falha ao conectar ao GitHub Gist: {e}")
            
    # Fallback local se o Gist não estiver configurado
    if os.path.exists("local_db.json"):
        try:
            with open("local_db.json", "r", encoding="utf-8") as f:
                _local_cache = json.load(f)
            logger.info("Utilizando backup local_db.json.")
        except json.JSONDecodeError:
            logger.error("Erro ao fazer parse do backup local_db.json.")
            _local_cache = {}
        except Exception as e:
            logger.error(f"Erro ao ler local_db.json: {e}")
            _local_cache = {}

def db_get(path: str, default=None):
    """Lê dados instantaneamente a partir do cache de memória local sincronizado (O(1))."""
    keys = path.strip("/").split("/")
    temp = _local_cache
    try:
        for k in keys:
            temp = temp[k]
        return temp
    except (KeyError, TypeError):
        return default

def db_set(path: str, value) -> bool:
    """Atualiza o cache local, salva o backup em disco e envia as alterações para o Gist em tempo real de forma segura."""
    global _local_cache
    keys = path.strip("/").split("/")
    temp = _local_cache
    for k in keys[:-1]:
        if k not in temp or not isinstance(temp[k], dict):
            temp[k] = {}
        temp = temp[k]
    temp[keys[-1]] = value
    
    # Grava no backup local_db.json
    try:
        with open("local_db.json", "w", encoding="utf-8") as f:
            json.dump(_local_cache, f, indent=4, ensure_ascii=False)
    except IOError as e:
        logger.error(f"Erro ao escrever local_db.json: {e}")
        return False
    except Exception as e:
        logger.error(f"Erro inesperado ao escrever local_db.json: {e}")
        return False
        
    # Sincroniza com a nuvem de forma imediata (Garante que nenhum registro seja perdido ao reiniciar)
    if GITHUB_TOKEN and GIST_ID:
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        url = f"https://api.github.com/gists/{GIST_ID}"
        payload = {
            "description": "Banco de dados do scn_bot",
            "files": {
                "database.json": {
                    "content": json.dumps(_local_cache, indent=4, ensure_ascii=False)
                }
            }
        }
        try:
            resp = requests.patch(url, headers=headers, json=payload, timeout=5)
            if resp.status_code == 200:
                logger.info("Alterações sincronizadas e seguras no GitHub Gist.")
                return True
            else:
                logger.warning(f"Status {resp.status_code} ao fazer PATCH no GitHub.")
                return False
        except requests.exceptions.Timeout:
            logger.warning("Timeout ao sincronizar com GitHub (5s). Dados salvos localmente.")
            return True
        except requests.exceptions.ConnectionError:
            logger.warning("Erro de conexão ao GitHub. Dados salvos localmente.")
            return True
        except Exception as e:
            logger.error(f"Erro ao sincronizar com GitHub: {e}")
            return True
            
    return not (GITHUB_TOKEN and GIST_ID)


# --- SISTEMA DE TEMAS PARA PERFIL ---

TEMAS_DISPONIVEIS = {
    "dark": {
        "nome": "🌙 Dark",
        "fundo": "#1a1a1a",
        "texto_principal": "#ffffff",
        "texto_secundario": "#b0b0b0",
        "caixa_bio": (20, 20, 20, 130)
    },
    "light": {
        "nome": "☀️ Light",
        "fundo": "#f5f5f5",
        "texto_principal": "#1a1a1a",
        "texto_secundario": "#4a4a4a",
        "caixa_bio": (240, 240, 240, 180)
    },
    "neon": {
        "nome": "⚡ Neon",
        "fundo": "#0d0221",
        "texto_principal": "#ff006e",
        "texto_secundario": "#8338ec",
        "caixa_bio": (16, 2, 45, 140)
    },
    "ocean": {
        "nome": "🌊 Ocean",
        "fundo": "#0a1128",
        "texto_principal": "#00d9ff",
        "texto_secundario": "#0099cc",
        "caixa_bio": (5, 20, 60, 130)
    },
    "sunset": {
        "nome": "🌅 Sunset",
        "fundo": "#ff6b35",
        "texto_principal": "#fff8f0",
        "texto_secundario": "#ffd700",
        "caixa_bio": (255, 100, 20, 140)
    },
    "forest": {
        "nome": "🌲 Forest",
        "fundo": "#1b4332",
        "texto_principal": "#95d5b2",
        "texto_secundario": "#52b788",
        "caixa_bio": (20, 40, 25, 130)
    }
}

def obter_tema(user_data: dict, nome_tema: str = None):
    """Retorna as cores do tema. Se não existir ou for inválido, usa Dark."""
    if nome_tema is None:
        nome_tema = user_data.get("tema", "dark")
    
    if nome_tema not in TEMAS_DISPONIVEIS:
        nome_tema = "dark"
    
    return TEMAS_DISPONIVEIS[nome_tema]

def obter_registro(discord_id: int) -> dict:
    """Busca as informações de um usuário com base no ID do Discord."""
    return db_get(f"users/{discord_id}")

def obter_ou_auto_registrar(user: discord.User, guild_id: str = "DirectMessage") -> dict:
    """Retorna os dados do usuário. Se ele não for cadastrado, registra-o automaticamente de forma invisível."""
    user_id = str(user.id)
    user_data = db_get(f"users/{user_id}")
    if user_data:
        return user_data
        
    # Registro automático dinâmico no primeiro comando ativado
    if user.id == DONO_BOT_ID:
        bot_id = 0
    else:
        next_id = db_get("global_config/proximo_bot_id", 1)
        bot_id = next_id
        db_set("global_config/proximo_bot_id", next_id + 1)
        
    novo_cadastro = {
        "discord_id": user_id,
        "guild_id": guild_id,
        "bot_id": bot_id,
        "nome": user.display_name,
        "tema": "dark",  # Tema padrão
        "perfil": {
            "fundo": "#2f3136",
            "fundo_url": "",
            "avatar_pos": "superior_esquerdo",
            "descricao": "Nenhuma descrição definida ainda. Use #perfil-config para personalizar!"
        }
    }
    db_set(f"users/{user_id}", novo_cadastro)
    logger.info(f"Auto-Registro: Usuário {user.name} ({user_id}) registrado com ID interno #{bot_id}.")
    return novo_cadastro


# --- FUNÇÕES DE PERSISTÊNCIA DA SENHA DO FLASK ---

def obter_senha_admin() -> str:
    """Busca a senha master gravada."""
    return db_get("admin_config/password", "")

def salvar_senha_admin(senha: str) -> bool:
    """Grava a senha master permanentemente."""
    try:
        with open(SENHA_ADMIN_FILE, "w", encoding="utf-8") as f:
            f.write(senha.strip())
    except IOError as e:
        logger.warning(f"Não foi possível salvar senha_admin.txt: {e}")
    except Exception as e:
        logger.error(f"Erro inesperado ao salvar senha_admin.txt: {e}")
    return db_set("admin_config/password", senha.strip())

def existe_senha_admin() -> bool:
    """Verifica se já existe uma senha configurada no banco."""
    return len(obter_senha_admin()) > 0


# --- MÓDULO DE RENDEREZAÇÃO DO CARTÃO DE PERFIL (PILLOW) ---

def gerar_imagem_perfil(nome: str, bot_id: int, avatar_url: str, pos: str, descricao: str, fundo_cor: str, fundo_url: str = None, tema: dict = None) -> io.BytesIO:
    """Desenha dinamicamente o cartão de perfil com suporte a temas."""
    if tema is None:
        tema = TEMAS_DISPONIVEIS["dark"]
    
    W, H = 600, 300
    
    # Carrega plano de fundo (URL tem prioridade)
    if fundo_url:
        try:
            resp = requests.get(fundo_url, timeout=3)
            img_fundo = Image.open(io.BytesIO(resp.content)).convert("RGBA")
            img_fundo = img_fundo.resize((W, H))
        except requests.exceptions.RequestException:
            img_fundo = Image.new("RGBA", (W, H), fundo_cor)
    else:
        try:
            img_fundo = Image.new("RGBA", (W, H), fundo_cor)
        except Exception:
            img_fundo = Image.new("RGBA", (W, H), "#2f3136")
            
    draw = ImageDraw.Draw(img_fundo)
    
    # Caixa semi-transparente para contraste do texto (usa cor do tema)
    caixa_cor = tema.get("caixa_bio", (0, 0, 0, 110))
    draw.rectangle([20, 200, 580, 280], fill=caixa_cor)
    
    # Carrega e posiciona avatar (redondo)
    avatar_size = 120
    try:
        resp_av = requests.get(avatar_url, timeout=3)
        img_avatar = Image.open(io.BytesIO(resp_av.content)).convert("RGBA")
        img_avatar = img_avatar.resize((avatar_size, avatar_size))
        
        # Cria máscara circular
        mascara = Image.new("L", (avatar_size, avatar_size), 0)
        draw_mascara = ImageDraw.Draw(mascara)
        draw_mascara.ellipse((0, 0, avatar_size, avatar_size), fill=255)
        
        # Posicionamento com padding
        pad = 20
        posicoes = {
            "superior_esquerdo": (pad, pad),
            "se": (pad, pad),
            "superior_direito": (W - avatar_size - pad, pad),
            "sd": (W - avatar_size - pad, pad),
            "inferior_esquerdo": (pad, H - avatar_size - pad),
            "ie": (pad, H - avatar_size - pad),
            "inferior_direito": (W - avatar_size - pad, H - avatar_size - pad),
            "id": (W - avatar_size - pad, H - avatar_size - pad),
            "centro": ((W - avatar_size) // 2, (H - avatar_size) // 2),
            "c": ((W - avatar_size) // 2, (H - avatar_size) // 2)
        }
        coords = posicoes.get(pos, (pad, pad))
        img_fundo.paste(img_avatar, coords, mascara)
    except requests.exceptions.RequestException as e:
        logger.warning(f"Não consegui baixar avatar: {e}")
        
    # Texto com fontes do sistema (com fallback)
    fonte_nome = None
    fonte_desc = None
    try:
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "arial.ttf",
            "C:\\Windows\\Fonts\\arial.ttf"
        ]
        for path in font_paths:
            if os.path.exists(path):
                fonte_nome = ImageFont.truetype(path, 20)
                fonte_desc = ImageFont.truetype(path, 14)
                break
    except Exception as e:
        logger.warning(f"Não consegui carregar fonte: {e}")
        
    if fonte_nome is None:
        fonte_nome = ImageFont.load_default()
    if fonte_desc is None:
        fonte_desc = ImageFont.load_default()
    
    # Cores do tema
    cor_nome = tema.get("texto_principal", "#ffffff")
    cor_desc = tema.get("texto_secundario", "#b0b0b0")
    
    # Define posição do texto de forma estrita baseada na posição do avatar para não sobrepor
    text_x = 160 if pos in ["superior_esquerdo", "inferior_esquerdo", "se", "ie"] else 40
    text_y = 40 if pos in ["superior_esquerdo", "superior_direito", "se", "sd"] else 120
    
    # Desenha nome e ID
    text_nome = f"{nome}"
    draw.text((text_x, text_y), text_nome, fill=cor_nome, font=fonte_nome)
    draw.text((text_x, text_y + 30), f"ID no Bot: #{bot_id}", fill="#5865F2", font=fonte_nome)
    
    # Desenha descrição com quebra de linha
    linhas_desc = [descricao[i:i+55] for i in range(0, len(descricao), 55)]
    y_desc = 210
    for linha in linhas_desc[:3]:  # Máximo 3 linhas de biografia
        draw.text((30, y_desc), linha, fill=cor_desc, font=fonte_desc)
        y_desc += 22
    
    # Retorna em BytesIO
    buffer = io.BytesIO()
    img_fundo.convert("RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


# --- MÓDULO DE MÉTRICAS E TRAQUEAMENTO DE COMANDOS ---

def registrar_execucao_comando():
    """Incrementa o contador de comandos no arquivo temporário, respeitando a janela de 1h."""
    agora = time.time()
    dados = {"timestamp_inicial": agora, "quantidade": 0}
    
    if os.path.exists(COMANDOS_TMP_FILE):
        try:
            with open(COMANDOS_TMP_FILE, "r", encoding="utf-8") as f:
                dados = json.load(f)
        except Exception:
            pass
            
    # Se o arquivo foi criado há mais de 1 hora (3600 segundos), resetamos o ciclo [2]
    if agora - dados.get("timestamp_inicial", agora) > 3600:
        dados = {"timestamp_inicial": agora, "quantidade": 1}
    else:
        dados["quantidade"] = dados.get("quantidade", 0) + 1
        
    try:
        with open(COMANDOS_TMP_FILE, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=4)
    except Exception as e:
        logger.error(f"Falha ao gravar comandos_hora.tmp: {e}")

def obter_metricas_comandos() -> tuple:
    """Retorna a quantidade acumulada e a média de comandos por hora (Quantidade / 2)."""
    if not os.path.exists(COMANDOS_TMP_FILE):
        return 0, 0.0
        
    try:
        with open(COMANDOS_TMP_FILE, "r", encoding="utf-8") as f:
            dados = json.load(f)
            
        agora = time.time()
        if agora - dados.get("timestamp_inicial", agora) > 3600:
            try:
                os.remove(COMANDOS_TMP_FILE)
            except Exception:
                pass
            return 0, 0.0
            
        quantidade = dados.get("quantidade", 0)
        media = quantidade / 2.0 if quantidade > 0 else 0.0
        return quantidade, media
    except Exception:
        return 0, 0.0


# --- ROTAS DO FLASK ---

@app.route("/")
def index():
    return redirect(url_for("admin"))


@app.route("/admin", methods=["GET", "POST"])
def admin():
    # Rate limiting
    client_ip = request.remote_addr
    if not check_rate_limit(client_ip):
        logger.warning(f"Rate limit excedido para IP {client_ip}")
        return "⚠️ Muitas requisições. Tente novamente em alguns minutos.", 429
    
    # Caso 1: Primeiro acesso
    if not existe_senha_admin():
        if request.method == "POST":
            senha_definida = request.form.get("senha")
            if senha_definida:
                salvar_senha_admin(senha_definida)
                session["logado"] = True
                return redirect(url_for("admin"))
        
        return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Configuração do Administrador</title>
            <style>
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #1e1e1e; color: #f5f5f5; text-align: center; margin-top: 100px; }
                .container { background-color: #2d2d2d; max-width: 400px; margin: 0 auto; padding: 30px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
                input[type="password"] { padding: 12px; font-size: 16px; margin: 15px 0; border-radius: 5px; border: 1px solid #444; background-color: #111; color: #fff; width: 80%; }
                input[type="submit"] { padding: 12px 24px; font-size: 16px; border-radius: 5px; border: none; background-color: #5865F2; color: #fff; cursor: pointer; transition: 0.2s; }
                input[type="submit"]:hover { background-color: #4752c4; }
            </style>
        </head>
        <body>
            <div class="container">
                <h2>🔒 Configurar Senha</h2>
                <p>Nenhuma senha foi encontrada. Defina a senha master para o painel administrativo.</p>
                <form method="POST">
                    <input type="password" name="senha" placeholder="Nova senha administrativa" required><br>
                    <input type="submit" value="Salvar Senha e Entrar">
                </form>
            </div>
        </body>
        </html>
        """)

    # Caso 2: Login na sessão pendente
    if not session.get("logado"):
        if request.method == "POST":
            senha_digitada = request.form.get("senha")
            senha_salva = obter_senha_admin()
            
            if senha_digitada == senha_salva:
                session["logado"] = True
                return redirect(url_for("admin"))
            else:
                return render_template_string("""
                <div style="text-align:center; margin-top:50px; font-family:sans-serif; background-color: #1e1e1e; color: #fff; height: 100vh; padding-top: 50px;">
                    <h3 style="color:#ff3333;">❌ Senha incorreta!</h3>
                    <a href="/admin" style="color:#5865F2; text-decoration:none;">Tentar novamente</a>
                </div>
                """)
        
        return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Login Administrativo</title>
            <style>
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #1e1e1e; color: #f5f5f5; text-align: center; margin-top: 100px; }
                .container { background-color: #2d2d2d; max-width: 400px; margin: 0 auto; padding: 30px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
                input[type="password"] { padding: 12px; font-size: 16px; margin: 15px 0; border-radius: 5px; border: 1px solid #444; background-color: #111; color: #fff; width: 80%; }
                input[type="submit"] { padding: 12px 24px; font-size: 16px; border-radius: 5px; border: none; background-color: #5865F2; color: #fff; cursor: pointer; transition: 0.2s; }
                input[type="submit"]:hover { background-color: #4752c4; }
            </style>
        </head>
        <body>
            <div class="container">
                <h2>🔑 Login de Administrador</h2>
                <p>Identifique-se para acessar o painel administrativo do bot.</p>
                <form method="POST">
                    <input type="password" name="senha" placeholder="Digite a senha" required><br>
                    <input type="submit" value="Entrar">
                </form>
            </div>
        </body>
        </html>
        """)

    # Caso 3: Dashboard ativo
    bot_online = bot.is_ready()
    latencia = f"{bot.latency * 1000:.0f}ms" if bot_online else "N/A"
    servidores_count = len(bot.guilds) if bot_online else 0
    
    # Lista de nomes de todos os servidores que o bot está conectado
    nomes_servidores = [g.name for g in bot.guilds] if bot_online else []
    
    # Métrica de comandos ativos na última 1h
    qtd_comandos, media_comandos = obter_metricas_comandos()

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Dashboard Administrativa</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #131416; color: #e3e5e8; margin: 40px; }
            h1 { color: #5865f2; margin-bottom: 30px; }
            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; }
            .card { background-color: #2f3136; padding: 25px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.3); border-left: 5px solid #5865f2; }
            .card h3 { margin-top: 0; color: #b9bbbe; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; }
            .card .valor { font-size: 28px; font-weight: bold; margin: 10px 0; }
            .status-on { color: #43b581; }
            .status-off { color: #f04747; }
            
            .server-section { margin-top: 40px; background-color: #2f3136; padding: 25px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.3); }
            .server-section h2 { margin-top: 0; color: #5865f2; border-bottom: 1px solid #4f545c; padding-bottom: 10px; }
            .server-list { list-style-type: none; padding: 0; margin-top: 15px; }
            .server-list li { padding: 8px 12px; background-color: #202225; margin-bottom: 8px; border-radius: 4px; border-left: 3px solid #43b581; font-weight: 500; }
            .server-list li:nth-child(even) { border-left-color: #faa61a; }
            
            .logout-btn { display: inline-block; margin-top: 30px; padding: 10px 20px; background-color: #f04747; color: white; text-decoration: none; border-radius: 4px; transition: 0.2s; font-weight: bold; }
            .logout-btn:hover { background-color: #d83c3e; }
        </style>
    </head>
    <body>
        <h1>⚙️ scn_bot - Painel Administrativo</h1>
        <div class="grid">
            <div class="card">
                <h3>Status do Bot</h3>
                <div class="valor {% if bot_online %}status-on{% else %}status-off{% endif %}">
                    {{ 'ONLINE' if bot_online else 'OFFLINE' }}
                </div>
            </div>
            <div class="card">
                <h3>Latência da API</h3>
                <div class="valor">{{ latencia }}</div>
            </div>
            <div class="card">
                <h3>Servidores Conectados</h3>
                <div class="valor">{{ servidores_count }}</div>
            </div>
            <div class="card" style="border-left-color: #faa61a;">
                <h3>Média Comandos/Hora</h3>
                <div class="valor">{{ "%.1f"|format(media_comandos) }}</div>
                <p style="color: #b9bbbe; font-size: 11px; margin: 0;">Total acumulado na hora: {{ qtd_comandos }}</p>
            </div>
        </div>
        
        <div class="server-section">
            <h2>🖥️ Servidores Conectados Ativos</h2>
            {% if nomes_servidores %}
                <ul class="server-list">
                    {% for name in nomes_servidores %}
                        <li>{{ name }}</li>
                    {% endfor %}
                </ul>
            {% else %}
                <p style="color: #b9bbbe; font-style: italic;">O bot não está conectado a nenhum servidor de momento.</p>
            {% endif %}
        </div>
        
        <a class="logout-btn" href="/logout">🚪 Sair do Painel</a>
    </body>
    </html>
    """, bot_online=bot_online, latencia=latencia, servidores_count=servidores_count, 
       nomes_servidores=nomes_servidores, qtd_comandos=qtd_comandos, media_comandos=media_comandos)


@app.route("/logout")
def logout():
    session.pop("logado", None)
    return redirect(url_for("admin"))


# --- VIEW INTERATIVA: PAINEL ADMINISTRATIVO MASTER ---

class PainelAdminView(discord.ui.View):
    def __init__(self, bot_inst: commands.Bot, autor_id: int):
        super().__init__(timeout=180)
        self.bot = bot_inst
        self.autor_id = autor_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Garante segurança estrita de bloqueio contra acessos não autorizados [1]
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message("❌ **Acesso negado!** Apenas o administrador master do bot pode clicar nos botões.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Status do Sistema", style=discord.ButtonStyle.primary, emoji="🖥️")
    async def status_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        bot_online = self.bot.is_ready()
        latencia = f"{self.bot.latency * 1000:.0f}ms" if bot_online else "N/A"
        servidores = len(self.bot.guilds)
        
        # Puxa quantidade de cadastros no cache local
        users_db = db_get("users", {})
        total_usuarios = len(users_db) if isinstance(users_db, dict) else 0
        
        next_id = db_get("global_config/proximo_bot_id", 1)
        
        embed = discord.Embed(
            title="🖥️ Status Detalhado do Sistema",
            color=discord.Color.blue()
        )
        embed.add_field(name="Status da API", value="`ONLINE`" if bot_online else "`OFFLINE`", inline=True)
        embed.add_field(name="Ping da API", value=f"`{latencia}`", inline=True)
        embed.add_field(name="Guildas Conectadas", value=f"`{servidores}`", inline=True)
        embed.add_field(name="Registros Totais (Banco)", value=f"`{total_usuarios}`", inline=True)
        embed.add_field(name="Próximo ID Global", value=f"`#{next_id}`", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Forçar Sync Gist", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def sync_gist_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            sincronizar_banco_local()
            await interaction.followup.send("✅ **Sincronização forçada concluída!** O cache de RAM e o arquivo local foram atualizados com a nuvem do Gist.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Falha técnica ao sincronizar: {e}", ephemeral=True)

    @discord.ui.button(label="Baixar Backup", style=discord.ButtonStyle.secondary, emoji="📁")
    async def backup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if os.path.exists("local_db.json"):
            file = discord.File("local_db.json")
            await interaction.response.send_message(content="📄 **Backup gerado!** Aqui está a cópia em tempo real do seu banco de dados local cacheado:", file=file, ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nenhum arquivo de backup local `local_db.json` foi localizado no servidor.", ephemeral=True)

    @discord.ui.button(label="Sincronizar Slash (/) ", style=discord.ButtonStyle.success, emoji="🔨")
    async def sync_slash_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            synced = await self.bot.tree.sync()
            await interaction.followup.send(f"✅ **Árvore de comandos sincronizada!** `{len(synced)}` comandos de barra atualizados globalmente no Discord.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro de upload de comandos: {e}", ephemeral=True)

    @discord.ui.button(label="Ver Registros", style=discord.ButtonStyle.primary, emoji="👥")
    async def ver_registros_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        users_db = db_get("users", {})
        if not users_db or not isinstance(users_db, dict):
            await interaction.response.send_message("🔍 Nenhum registro foi localizado na base de dados até o momento.", ephemeral=True)
            return
            
        lista = []
        for d_id, dados in users_db.items():
            nome = dados.get("nome", "Desconhecido")
            bot_id = dados.get("bot_id", "?")
            lista.append(f"• ID Bot: `#{bot_id}` | `{nome}` (Discord: <@{d_id}>)")
            
        texto_lista = "\n".join(lista[:15])  # Limita a 15 para evitar quebras por limite de caracteres da Embed
        if len(lista) > 15:
            texto_lista += f"\n*... e outros {len(lista) - 15} registros.*"
            
        embed = discord.Embed(
            title="👥 Diretório de Usuários Cadastrados",
            description=texto_lista if lista else "Diretório vazio.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


# --- COMANDOS: UTILS ---

@bot.command(name="ping")
async def ping_prefix(ctx: commands.Context):
    """Mede a latência de resposta do bot do Discord."""
    latencia = round(bot.latency * 1000)
    await ctx.send(f"🏓 **Pong!** Minha latência é de `{latencia}ms`.")

@bot.tree.command(name="ping", description="Verifica a latência atual do bot.")
async def ping_slash(interaction: discord.Interaction):
    """Mede a latência em tempo real."""
    latencia = f"{bot.latency * 1000:.0f}ms"
    await interaction.response.send_message(f"🎲 **Pong!** Minha latência de API está em `{latencia}`.", ephemeral=True)


# --- COMANDOS DE DIAGNÓSTICO E ESCRITA (I/O) NO GIST ---

@bot.command(name="io-write")
async def io_write_prefix(ctx: commands.Context, *, texto: str = None):
    """Grava uma mensagem de teste no banco de dados do GitHub Gist."""
    if not texto:
        await ctx.send("❌ **Faltou o texto!** Escreva algo após o comando para que eu grave. Exemplo: `#io-write Teste de Conexão`")
        return

    sucesso = db_set("teste/mensagem", texto)
    if sucesso:
        await ctx.send(f"✅ **Escrita no GitHub Gist bem-sucedida!** Gravei a informação abaixo:\n`{texto}`")
    else:
        await ctx.send("❌ **Falha ao salvar no banco!** Verifique as chaves e os logs do terminal.")

@bot.tree.command(name="io-write", description="Grava um texto de teste no banco de dados em nuvem.")
@app_commands.describe(texto="O texto que deseja salvar de forma persistente.")
async def io_write_slash(interaction: discord.Interaction, texto: str):
    """Grava no Gist usando o comando de barra (/) com resposta efêmera."""
    sucesso = db_set("teste/mensagem", texto)
    if sucesso:
        await interaction.response.send_message(f"✅ **Escrita no Gist bem-sucedida de forma privada!** Texto salvo:\n`{texto}`", ephemeral=True)
    else:
        await interaction.response.send_message("❌ **Falha ao salvar no banco de dados em nuvem.**", ephemeral=True)

@bot.command(name="io-read")
async def io_read_prefix(ctx: commands.Context):
    """Lê a mensagem de teste salva no banco de dados do GitHub Gist."""
    texto = db_get("teste/mensagem")
    if texto:
        await ctx.send(f"📖 **Mensagem resgatada da nuvem com sucesso!**\n`{texto}`")
    else:
        await ctx.send("🔍 Nenhuma informação encontrada sob o caminho de testes. Utilize `#io-write <texto>` para cadastrar algo primeiro!")

@bot.tree.command(name="io-read", description="Lê o texto de teste salvo no banco de dados em nuvem.")
async def io_read_slash(interaction: discord.Interaction):
    """Lê a mensagem salva no Gist usando o comando de barra (/) com resposta efêmera."""
    texto = db_get("teste/mensagem")
    if texto:
        await interaction.response.send_message(f"📖 **Mensagem privada resgatada da nuvem:**\n`{texto}`", ephemeral=True)
    else:
        await interaction.response.send_message("🔍 Nenhuma informação de testes encontrada na nuvem.", ephemeral=True)


# --- COMANDOS: VARIANTES DO REGISTRO EM BANCO ---

# 1. COMANDO #registrar / /registrar
@bot.command(name="registrar")
async def registrar_prefix(ctx: commands.Context):
    user_id = str(ctx.author.id)
    guild_id = str(ctx.guild.id) if ctx.guild else "DirectMessage"
    
    # Verifica duplicidade no banco
    user_data = db_get(f"users/{user_id}")
    if user_data:
        await ctx.send(f"⚠️ **Você já está registrado!** Seu ID de usuário no bot é `#{user_data['bot_id']}`.")
        return

    if ctx.author.id == DONO_BOT_ID:
        bot_id = 0  # Dono do bot possui de forma estrita o ID 0
    else:
        # Incrementa o contador na nuvem para evitar colisões
        next_id = db_get("global_config/proximo_bot_id", 1)
        bot_id = next_id
        db_set("global_config/proximo_bot_id", next_id + 1)
        
    novo_cadastro = {
        "discord_id": user_id,
        "guild_id": guild_id,
        "bot_id": bot_id
    }
    db_set(f"users/{user_id}", novo_cadastro)
    
    embed = discord.Embed(
        title="🎉 Registro Concluído com Sucesso!",
        description=f"Seja bem-vindo, {ctx.author.mention}!",
        color=discord.Color.green()
    )
    embed.add_field(name="ID no Bot", value=f"`#{bot_id}`", inline=True)
    embed.add_field(name="ID do Discord", value=f"`{user_id}`", inline=True)
    if ctx.guild:
        embed.add_field(name="Servidor Principal", value=f"`{ctx.guild.name}`", inline=False)
        
    await ctx.send(embed=embed)

@bot.tree.command(name="registrar", description="Registra seu usuário globalmente no banco de dados do bot.")
async def registrar_slash(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild.id) if interaction.guild else "DirectMessage"
    
    user_data = db_get(f"users/{user_id}")
    if user_data:
        await interaction.response.send_message(f"⚠️ **Você já está registrado!** Seu ID de usuário no bot é `#{user_data['bot_id']}`.", ephemeral=True)
        return

    if interaction.user.id == DONO_BOT_ID:
        bot_id = 0
    else:
        next_id = db_get("global_config/proximo_bot_id", 1)
        bot_id = next_id
        db_set("global_config/proximo_bot_id", next_id + 1)
        
    novo_cadastro = {
        "discord_id": user_id,
        "guild_id": guild_id,
        "bot_id": bot_id
    }
    db_set(f"users/{user_id}", novo_cadastro)
    
    embed = discord.Embed(
        title="🎉 Registro Concluído com Sucesso!",
        description=f"Seja bem-vindo, {interaction.user.mention}!",
        color=discord.Color.green()
    )
    embed.add_field(name="ID no Bot", value=f"`#{bot_id}`", inline=True)
    embed.add_field(name="ID do Discord", value=f"`{user_id}`", inline=True)
    
    await interaction.response.send_message(embed=embed)


# 2. COMANDO #registrar-servidor / /registrar-servidor
@bot.command(name="registrar-servidor")
async def registrar_servidor_prefix(ctx: commands.Context, membro: discord.Member = None, *, nomes_raw: str = None):
    if not ctx.guild:
        await ctx.send("❌ Este comando só pode ser utilizado dentro de um servidor.")
        return

    if not membro:
        await ctx.send("❌ **Parâmetro incorreto!** Uso: `#registrar-servidor [@membro ou ID] [nome] [nome1]...`")
        return

    guild_id = str(ctx.guild.id)
    
    # Auto-registra o executor do comando
    autor_data = obter_ou_auto_registrar(ctx.author, guild_id)
    bot_id_owner = autor_data["bot_id"]
    
    # Sorteador de ID sequencial e persistente de cada ficha salvamento por Servidor
    server_config = db_get(f"server_config/{guild_id}", {})
    proximo_reg_id = server_config.get("proximo_registro_id", 1)
    server_config["proximo_registro_id"] = proximo_reg_id + 1
    db_set(f"server_config/{guild_id}", server_config)

    # Rótulos (Labels) customizáveis mapeados no banco (Default herda de Nome a Nome5)
    labels_padrao = {
        "0": "Nome",
        "1": "Nome1",
        "2": "Nome2",
        "3": "Nome3",
        "4": "Nome4",
        "5": "Nome5"
    }
    labels = server_config.get("labels", labels_padrao)
    for key, value in labels_padrao.items():
        if key not in labels:
            labels[key] = value

    permissao_geral = server_config.get("permissao_registrar_servidor", True)
    
    pode_executar = False
    if ctx.author.id == DONO_BOT_ID:
        pode_executar = True
    elif ctx.author.id == ctx.guild.owner_id:
        pode_executar = True
    elif ctx.author.guild_permissions.administrator:
        pode_executar = True
    elif permissao_geral:
        pode_executar = True

    if not pode_executar:
        await ctx.send("❌ **Acesso negado!** Apenas administradores ou o dono do servidor podem registrar membros neste servidor.")
        return

    nomes = nomes_raw.split() if nomes_raw else []
    while len(nomes) < 6:
        nomes.append("Não Definido")
    nomes = nomes[:6]

    registro_membro = {
        "id": proximo_reg_id,
        "owner": bot_id_owner,  # Salva o id interno no bot de quem registrou
        "server_id": guild_id,
        "owner_id": ctx.guild.owner_id,
        "registered_user_id": str(membro.id),
        "nome": nomes[0],
        "nome1": nomes[1],
        "nome2": nomes[2],
        "nome3": nomes[3],
        "nome4": nomes[4],
        "nome5": nomes[5]
    }
    
    db_set(f"server_registrations/{guild_id}/{membro.id}", registro_membro)
    
    embed = discord.Embed(
        title=f"📋 Ficha de Servidor Registrada (ID: #{proximo_reg_id})!",
        description=f"O membro {membro.mention} recebeu uma ficha de registro associada a este servidor.",
        color=discord.Color.blue()
    )
    embed.add_field(name=labels["0"], value=f"`{nomes[0]}`", inline=True)
    embed.add_field(name=labels["1"], value=f"`{nomes[1]}`", inline=True)
    embed.add_field(name=labels["2"], value=f"`{nomes[2]}`", inline=True)
    embed.add_field(name=labels["3"], value=f"`{nomes[3]}`", inline=True)
    embed.add_field(name=labels["4"], value=f"`{nomes[4]}`", inline=True)
    embed.add_field(name=labels["5"], value=f"`{nomes[5]}`", inline=True)
    
    await ctx.send(embed=embed)

@bot.tree.command(name="registrar-servidor", description="Cria uma ficha de registro personalizada para um membro no servidor.")
@app_commands.describe(
    membro="Membro a ser registrado.",
    nome="Valor do campo 0.",
    nome1="Valor do campo 1.",
    nome2="Valor do campo 2.",
    nome3="Valor do campo 3.",
    nome4="Valor do campo 4.",
    nome5="Valor do campo 5."
)
async def registrar_servidor_slash(
    interaction: discord.Interaction,
    membro: discord.Member,
    nome: str,
    nome1: str = "Não Definido",
    nome2: str = "Não Definido",
    nome3: str = "Não Definido",
    nome4: str = "Não Definido",
    nome5: str = "Não Definido"
):
    if not interaction.guild:
        await interaction.response.send_message("❌ Este comando só pode ser utilizado dentro de um servidor.", ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    
    autor_data = obter_ou_auto_registrar(interaction.user, guild_id)
    bot_id_owner = autor_data["bot_id"]
    
    server_config = db_get(f"server_config/{guild_id}", {})
    proximo_reg_id = server_config.get("proximo_registro_id", 1)
    server_config["proximo_registro_id"] = proximo_reg_id + 1
    db_set(f"server_config/{guild_id}", server_config)

    labels_padrao = {
        "0": "Nome",
        "1": "Nome1",
        "2": "Nome2",
        "3": "Nome3",
        "4": "Nome4",
        "5": "Nome5"
    }
    labels = server_config.get("labels", labels_padrao)
    for key, value in labels_padrao.items():
        if key not in labels:
            labels[key] = value

    permissao_geral = server_config.get("permissao_registrar_servidor", True)
    
    pode_executar = False
    if interaction.user.id == DONO_BOT_ID:
        pode_executar = True
    elif interaction.user.id == interaction.guild.owner_id:
        pode_executar = True
    elif interaction.user.guild_permissions.administrator:
        pode_executar = True
    elif permissao_geral:
        pode_executar = True

    if not pode_executar:
        await interaction.response.send_message("❌ **Acesso negado!** Apenas administradores ou o dono do servidor podem registrar membros.", ephemeral=True)
        return

    registro_membro = {
        "id": proximo_reg_id,
        "owner": bot_id_owner,
        "server_id": guild_id,
        "owner_id": interaction.guild.owner_id,
        "registered_user_id": str(membro.id),
        "nome": nome,
        "nome1": nome1,
        "nome2": nome2,
        "nome3": nome3,
        "nome4": nome4,
        "nome5": nome5
    }
    
    db_set(f"server_registrations/{guild_id}/{membro.id}", registro_membro)
    
    embed = discord.Embed(
        title="📋 Ficha de Servidor Registrada!",
        description=f"O membro {membro.mention} recebeu uma ficha de registro associada a este servidor.",
        color=discord.Color.blue()
    )
    embed.add_field(name=labels["0"], value=f"`{nome}`", inline=True)
    embed.add_field(name=labels["1"], value=f"`{nome1}`", inline=True)
    embed.add_field(name=labels["2"], value=f"`{nome2}`", inline=True)
    embed.add_field(name=labels["3"], value=f"`{nome3}`", inline=True)
    embed.add_field(name=labels["4"], value=f"`{nome4}`", inline=True)
    embed.add_field(name=labels["5"], value=f"`{nome5}`", inline=True)
    
    await interaction.response.send_message(embed=embed)


# 3. COMANDO #registrar-config / /registrar-config
@bot.command(name="registrar-config")
async def registrar_config_prefix(ctx: commands.Context, sub_comando: str = None, *args):
    if not ctx.guild:
        await ctx.send("❌ Este comando só pode ser utilizado dentro de um servidor.")
        return

    guild_id = str(ctx.guild.id)
    
    permitido = False
    if ctx.author.id == DONO_BOT_ID:
        permitido = True
    elif ctx.author.id == ctx.guild.owner_id:
        permitido = True
    elif ctx.author.guild_permissions.administrator:
        permitido = True

    if not permitido:
        await ctx.send("❌ **Acesso negado!** Apenas o dono do bot, dono do servidor ou administradores com privilégios podem alterar essa configuração.")
        return

    if not sub_comando:
        await ctx.send("❌ **Como usar o comando:**\n"
                       "• `#registrar-config <True/False>` - Permissão geral para o comando `#registrar-servidor`.\n"
                       "• `#registrar-config label <0 a 5> <Novo Nome>` - Altera o nome/rótulo dos campos de registro.")
        return

    # Sub-comando 1: Configuração de Permissão Geral (True ou False)
    if sub_comando.lower() in ["true", "false", "sim", "nao", "não", "ativo", "ativado", "desativado", "inativo"]:
        val_bool = None
        sub_cmd = sub_comando.lower()
        if sub_cmd in ["true", "sim", "ativo", "ativado"]:
            val_bool = True
        elif sub_cmd in ["false", "nao", "não", "inativo", "desativado"]:
            val_bool = False

        server_config = db_get(f"server_config/{guild_id}", {})
        server_config["permissao_registrar_servidor"] = val_bool
        db_set(f"server_config/{guild_id}", server_config)

        status_txt = "PÚBLICO (Qualquer membro pode utilizar o #registrar-servidor)" if val_bool else "RESTRITO (Apenas administradores e donos podem registrar)"
        
        embed = discord.Embed(
            title="⚙️ Configurações do Servidor Atualizadas!",
            description="A política de controle de registro de membros foi alterada.",
            color=discord.Color.gold()
        )
        embed.add_field(name="Permissão Geral", value=f"`{status_txt}`", inline=False)
        await ctx.send(embed=embed)
        return

    # Sub-comando 2: Customização de Rótulos (Labels) dos Campos
    elif sub_comando.lower() == "label":
        if len(args) < 2:
            await ctx.send("❌ **Sintaxe incorreta!** Use: `#registrar-config label [0 a 5] [Novo Nome]`\n*Exemplo:* `#registrar-config label 0 Classe`")
            return
            
        index_str = args[0]
        if not index_str.isdigit() or int(index_str) < 0 or int(index_str) > 5:
            await ctx.send("❌ O índice do campo a ser modificado deve ser um número inteiro de **0 a 5**.")
            return
            
        index_campo = int(index_str)
        novo_nome_rótulo = " ".join(args[1:])

        server_config = db_get(f"server_config/{guild_id}", {})
        labels = server_config.get("labels", {})
        labels[str(index_campo)] = novo_nome_rótulo
        server_config["labels"] = labels
        db_set(f"server_config/{guild_id}", server_config)

        await ctx.send(f"✅ **Rótulo do Campo {index_campo} atualizado!** No registro do servidor, esse campo agora aparecerá como: **`{novo_nome_rótulo}`**.")
        return

    else:
        await ctx.send("❌ **Sub-comando desconhecido!** Digite `#registrar-config` para ver as opções válidas.")

@bot.tree.command(name="registrar-config", description="Altera as configurações de registro e customiza os campos do servidor.")
@app_commands.describe(
    permissao="True (Qualquer um registra) ou False (Apenas administradores).",
    campo_index="Número do campo a ser customizado (0 a 5).",
    campo_nome="Novo nome do rótulo de exibição para o campo selecionado."
)
async def registrar_config_slash(
    interaction: discord.Interaction,
    permissao: bool = None,
    campo_index: int = None,
    campo_nome: str = None
):
    if not interaction.guild:
        await interaction.response.send_message("❌ Este comando só pode ser utilizado dentro de um servidor.", ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    
    permitido = False
    if interaction.user.id == DONO_BOT_ID:
        permitido = True
    elif interaction.user.id == interaction.guild.owner_id:
        permitido = True
    elif interaction.user.guild_permissions.administrator:
        permitido = True

    if not permitido:
        await interaction.response.send_message("❌ **Acesso negado!** Apenas administradores ou o dono do servidor podem configurar permissões.", ephemeral=True)
        return

    server_config = db_get(f"server_config/{guild_id}", {})

    # Altera a permissão geral se fornecido o argumento
    if permissao is not None:
        server_config["permissao_registrar_servidor"] = permissao

    # Altera a customização de rótulo se fornecido o argumento
    if campo_index is not None and campo_nome is not None:
        if campo_index < 0 or campo_index > 5:
            await interaction.response.send_message("❌ O índice do campo de alteração deve estar entre 0 e 5.", ephemeral=True)
            return
        labels = server_config.get("labels", {})
        labels[str(campo_index)] = campo_nome
        server_config["labels"] = labels

    db_set(f"server_config/{guild_id}", server_config)

    status_txt = "Não Alterado" if permissao is None else ("PÚBLICO" if permissao else "RESTRITO")
    
    embed = discord.Embed(
        title="⚙️ Configurações do Servidor Atualizadas!",
        description="As políticas de registro foram atualizadas.",
        color=discord.Color.gold()
    )
    if permissao is not None:
        embed.add_field(name="Permissão Geral", value=f"`{status_txt}`", inline=False)
    if campo_index is not None and campo_nome is not None:
        embed.add_field(name=f"Campo Customizado [{campo_index}]", value=f"Nome alterado para: **`{campo_nome}`**", inline=False)

    await interaction.response.send_message(embed=embed)


# --- COMANDOS: PERSONALIZAÇÃO DE PERFIL ---

# 1. COMANDO #perfil / /perfil
@bot.command(name="perfil")
async def perfil_prefix(ctx: commands.Context):
    """Gera e exibe seu cartão de perfil personalizado."""
    user_data = obter_ou_auto_registrar(ctx.author, str(ctx.guild.id) if ctx.guild else "DirectMessage")
    
    bot_id = user_data["bot_id"]
    nome = user_data.get("nome", ctx.author.display_name)
    perfil = user_data.get("perfil", {})
    tema = obter_tema(user_data)
    
    fundo_cor = perfil.get("fundo", "#2f3136")
    fundo_url = perfil.get("fundo_url", "")
    pos = perfil.get("avatar_pos", "superior_esquerdo")
    descricao = perfil.get("descricao", "Nenhuma biografia definida ainda. Use #perfil-config para personalizar!")
    
    avatar_url = ctx.author.display_avatar.with_format("png").url

    async with ctx.typing():
        try:
            loop = asyncio.get_running_loop()
            buffer = await loop.run_in_executor(
                None, gerar_imagem_perfil, nome, bot_id, avatar_url, pos, descricao, fundo_cor, fundo_url, tema
            )
            file = discord.File(fp=buffer, filename="perfil.png")
            await ctx.send(file=file)
        except Exception as e:
            logger.error(f"Erro ao gerar perfil: {e}")
            embed = discord.Embed(
                title=f"👤 {nome} (ID: #{bot_id})",
                description=descricao,
                color=discord.Color.blue()
            )
            embed.add_field(name="📍 Posição Avatar", value=f"`{pos}`", inline=True)
            embed.add_field(name="🎨 Tema", value=f"`{user_data.get('tema', 'dark')}`", inline=True)
            await ctx.send("Não consegui renderizar como imagem, mas aqui está em outro formato:", embed=embed)

@bot.tree.command(name="perfil", description="Exibe o seu cartão de perfil personalizado em formato de imagem.")
async def perfil_slash(interaction: discord.Interaction):
    """Versão de comando de barra (/perfil) privada."""
    await interaction.response.defer(ephemeral=True)
    
    guild_id = str(interaction.guild.id) if interaction.guild else "DirectMessage"
    user_data = obter_ou_auto_registrar(interaction.user, guild_id)
    
    bot_id = user_data["bot_id"]
    nome = user_data.get("nome", interaction.user.display_name)
    perfil = user_data.get("perfil", {})
    tema = obter_tema(user_data)
    
    fundo_cor = perfil.get("fundo", "#2f3136")
    fundo_url = perfil.get("fundo_url", "")
    pos = perfil.get("avatar_pos", "superior_esquerdo")
    descricao = perfil.get("descricao", "Nenhuma biografia definida ainda. Use /perfil-config para personalizar!")
    
    avatar_url = interaction.user.display_avatar.with_format("png").url

    try:
        loop = asyncio.get_running_loop()
        buffer = await loop.run_in_executor(
            None, gerar_imagem_perfil, nome, bot_id, avatar_url, pos, descricao, fundo_cor, fundo_url, tema
        )
        file = discord.File(fp=buffer, filename="perfil.png")
        await interaction.followup.send(file=file, ephemeral=True)
    except Exception as e:
        logger.error(f"Erro ao gerar perfil slash: {e}")
        embed = discord.Embed(
            title=f"👤 {nome} (ID: #{bot_id})",
            description=descricao,
            color=discord.Color.blue()
        )
        embed.add_field(name="📍 Posição Avatar", value=f"`{pos}`", inline=True)
        embed.add_field(name="🎨 Tema", value=f"`{user_data.get('tema', 'dark')}`", inline=True)
        await interaction.followup.send("Não consegui renderizar como imagem, mas aqui está:", embed=embed, ephemeral=True)


# 1.5 COMANDO #perfil-tema / /perfil-tema
@bot.command(name="perfil-tema")
async def perfil_tema_prefix(ctx: commands.Context, tema: str = None):
    """Muda o tema visual do seu perfil."""
    if tema is None:
        lista_temas = "\n".join([f"• `{nome}` - {info['nome']}" for nome, info in TEMAS_DISPONIVEIS.items()])
        embed = discord.Embed(
            title="🎨 Temas Disponíveis",
            description=f"Use `#perfil-tema [nome]` para mudar\n\n{lista_temas}",
            color=discord.Color.purple()
        )
        await ctx.send(embed=embed)
        return
    
    if tema not in TEMAS_DISPONIVEIS:
        await ctx.send(f"❌ Tema `{tema}` não existe. Use `#perfil-tema` para ver opções.")
        return
    
    user_data = obter_ou_auto_registrar(ctx.author, str(ctx.guild.id) if ctx.guild else "DirectMessage")
    db_set(f"users/{ctx.author.id}/tema", tema)
    
    await ctx.send(f"✅ Tema alterado para `{TEMAS_DISPONIVEIS[tema]['nome']}`!")

@bot.tree.command(name="perfil-tema", description="Muda o tema visual do seu perfil.")
async def perfil_tema_slash(interaction: discord.Interaction, tema: str = None):
    """Versão slash do comando de tema."""
    if tema is None:
        lista_temas = "\n".join([f"• `{nome}` - {info['nome']}" for nome, info in TEMAS_DISPONIVEIS.items()])
        embed = discord.Embed(
            title="🎨 Temas Disponíveis",
            description=f"Escolha um tema\n\n{lista_temas}",
            color=discord.Color.purple()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if tema not in TEMAS_DISPONIVEIS:
        await interaction.response.send_message(f"❌ Tema `{tema}` não existe.", ephemeral=True)
        return
    
    user_data = obter_ou_auto_registrar(interaction.user, str(interaction.guild.id) if interaction.guild else "DirectMessage")
    db_set(f"users/{interaction.user.id}/tema", tema)
    
    await interaction.response.send_message(f"✅ Tema alterado para `{TEMAS_DISPONIVEIS[tema]['nome']}`!", ephemeral=True)


# 2. COMANDO #perfil-config / /perfil-config
@bot.command(name="perfil-config")
async def perfil_config_prefix(ctx: commands.Context, opcao: str = None, *, valor: str = None):
    """Personaliza as cores, links de fundo, biografia e posições da foto do seu perfil."""
    user_data = obter_ou_auto_registrar(ctx.author, str(ctx.guild.id) if ctx.guild else "DirectMessage")
    perfil = user_data.get("perfil", {})

    if not opcao or not valor:
        await ctx.send(
            "❌ **Sintaxe incorreta!** Use as opções abaixo:\n"
            "• `#perfil-config fundo <#Hex>` (Ex: `#perfil-config fundo #ff0000`)\n"
            "• `#perfil-config fundo_url <link>` (Ex: `#perfil-config fundo_url https://site.com/foto.jpg`)\n"
            "• `#perfil-config posicao <se | sd | ie | id>` (superior_esquerdo, superior_direito, inferior_esquerdo, inferior_direito)\n"
            "• `#perfil-config descricao <sua biografia>`"
        )
        return

    opcao_clean = opcao.lower().strip()
    valor_clean = valor.strip()

    if opcao_clean == "fundo":
        perfil["fundo"] = valor_clean
    elif opcao_clean == "fundo_url":
        perfil["fundo_url"] = valor_clean
    elif opcao_clean == "posicao":
        pos_map = {
            "se": "superior_esquerdo",
            "sd": "superior_direito",
            "ie": "inferior_esquerdo",
            "id": "inferior_direito",
            "superior_esquerdo": "superior_esquerdo",
            "superior_direito": "superior_direito",
            "inferior_esquerdo": "inferior_esquerdo",
            "inferior_direito": "inferior_direito"
        }
        val_pos = pos_map.get(valor_clean.lower().replace(" ", "_"))
        if val_pos:
            perfil["avatar_pos"] = val_pos
        else:
            await ctx.send("❌ Posição inválida! Escolha entre: `se` (sup. esquerdo), `sd` (sup. direito), `ie` (inf. esquerdo) ou `id` (inf. direito).")
            return
    elif opcao_clean in ["descricao", "desc"]:
        perfil["descricao"] = valor_clean
    else:
        await ctx.send("❌ Opção de configuração desconhecida! Escolha: `fundo`, `fundo_url`, `posicao` ou `descricao`.")
        return

    user_data["perfil"] = perfil
    db_set(f"users/{ctx.author.id}", user_data)
    await ctx.send("✅ **Configuração de perfil atualizada com sucesso!** Use `#perfil` para ver as mudanças.")

@bot.tree.command(name="perfil-config", description="Personaliza os aspectos visuais e a biografia do seu cartão de perfil.")
@app_commands.describe(
    fundo="Cor sólida em formato hexadecimal (Ex: #7289da).",
    fundo_url="Link direto de uma imagem para ser seu plano de fundo (Ex: .png ou .jpg).",
    posicao="Escolha em qual canto seu avatar será desenhado (se, sd, ie, id).",
    descricao="Sua nova biografia ou descrição de perfil."
)
async def perfil_config_slash(
    interaction: discord.Interaction,
    fundo: str = None,
    fundo_url: str = None,
    posicao: str = None,
    descricao: str = None
):
    guild_id = str(interaction.guild.id) if interaction.guild else "DirectMessage"
    user_data = obter_ou_auto_registrar(interaction.user, guild_id)
    perfil = user_data.get("perfil", {})

    if fundo:
        perfil["fundo"] = fundo.strip()
    if fundo_url:
        perfil["fundo_url"] = fundo_url.strip()
    if posicao:
        pos_map = {
            "se": "superior_esquerdo",
            "sd": "superior_direito",
            "ie": "inferior_esquerdo",
            "id": "inferior_direito",
            "superior_esquerdo": "superior_esquerdo",
            "superior_direito": "superior_direito",
            "inferior_esquerdo": "inferior_esquerdo",
            "inferior_direito": "inferior_direito"
        }
        val_pos = pos_map.get(posicao.lower().replace(" ", "_"))
        if val_pos:
            perfil["avatar_pos"] = val_pos
        else:
            await interaction.response.send_message("❌ Posição inválida! Escolha entre: `se` (sup. esquerdo), `sd` (sup. direito), `ie` (inf. esquerdo) ou `id` (inf. direito).", ephemeral=True)
            return
    if descricao:
        perfil["descricao"] = descricao.strip()

    user_data["perfil"] = perfil
    db_set(f"users/{interaction.user.id}", user_data)
    await interaction.response.send_message("✅ **Configurações do perfil salvas com sucesso!** Use `/perfil` para verificar o resultado.", ephemeral=True)


# --- COMANDO SECRETO DE AUTO-DESTRUIÇÃO: SENHA DO ADMIN ---

@bot.command(name="senha-adm")
async def senha_adm_prefix(ctx: commands.Context):
    """Envia a senha do painel na DM do Dono e a destrói em 5 segundos."""
    if ctx.author.id != DONO_BOT_ID:
        await ctx.send("❌ **Acesso restrito!** Apenas o desenvolvedor master do bot pode invocar este comando.")
        return

    senha_salva = obter_senha_admin()
    if not senha_salva:
        senha_salva = "Nenhuma senha cadastrada ainda. Acesse o site do bot no Render para criar!"

    try:
        canal_dm = await ctx.author.create_dm()
        mensagem_dm = await canal_dm.send(
            f"🔑 **[SEGURANÇA] Senha Administrativa do Painel Flask:**\n"
            f"`{senha_salva}`\n\n"
            f"*Esta mensagem será completamente apagada do servidor do Discord em **5 segundos**.*"
        )
        await ctx.send("✅ **Senha enviada na sua DM privada de forma segura!** Verifique agora.")
        
        await asyncio.sleep(5)
        await mensagem_dm.delete()
        
    except Exception as e:
        await ctx.send(f"❌ Não foi possível criar uma conexão de DM com você. Verifique se suas mensagens privadas estão liberadas! Detalhes: {e}")

@bot.tree.command(name="senha-adm", description="Envia a senha do painel do Flask na sua DM e a apaga após 5 segundos.")
async def senha_adm_slash(interaction: discord.Interaction):
    """Envia de forma privada na DM e se auto-destrói de acordo com o protocolo."""
    if interaction.user.id != DONO_BOT_ID:
        await interaction.response.send_message("❌ **Acesso restrito!** Apenas o desenvolvedor master do bot pode rodar este comando.", ephemeral=True)
        return

    senha_salva = obter_senha_admin()
    if not senha_salva:
        senha_salva = "Nenhuma senha cadastrada ainda. Acesse o site do bot no Render para criar!"

    try:
        canal_dm = await interaction.user.create_dm()
        mensagem_dm = await canal_dm.send(
            f"🔑 **[SEGURANÇA] Senha Administrativa do Painel Flask:**\n"
            f"`{senha_salva}`\n\n"
            f"*Esta mensagem será completamente apagada do servidor do Discord em **5 segundos**.*"
        )
        await interaction.response.send_message("✅ **Senha enviada de forma segura na sua DM privada!**", ephemeral=True)
        
        await asyncio.sleep(5)
        await mensagem_dm.delete()
        
    except Exception as e:
        await interaction.response.send_message(f"❌ Não consegui enviar DM. Verifique se as mensagens privadas estão abertas! Detalhes: {e}", ephemeral=True)


# --- NOVOS REQUISITOS: AUTO-ROLE POR BOTÕES & FAQ & ANTIRAID ---

# 1. AUTO-ROLE POR BOTÕES (#painel-cargos e /painel-cargos)
@bot.command(name="painel-cargos")
async def painel_cargos_prefix(ctx: commands.Context, titulo: str, descricao: str, *cargos: discord.Role):
    """Gera um painel com botões persistentes e vitalícios de cargos."""
    # Garante o auto-registro no primeiro comando usado
    obter_ou_auto_registrar(ctx.author, str(ctx.guild.id) if ctx.guild else "DirectMessage")

    if not (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID):
        await ctx.send("❌ **Acesso negado!** Apenas administradores do servidor podem criar painéis de cargos.")
        return
    if not cargos:
        await ctx.send("❌ Forneça pelo menos um cargo marcado! Exemplo: `#painel-cargos \"Cargos\" \"Escolha abaixo:\" @Notificacoes`")
        return

    embed = discord.Embed(title=titulo, description=descricao, color=discord.Color.blue())
    view = discord.ui.View(timeout=None)
    for r in cargos[:5]:  # Limita a 5 por questão de espaço e alinhamento
        view.add_item(discord.ui.Button(
            label=r.name,
            style=discord.ButtonStyle.secondary,
            custom_id=f"role_{r.id}"
        ))
    await ctx.send(embed=embed, view=view)

@bot.tree.command(name="painel-cargos", description="Gera um painel com botões de cargos selecionados.")
@app_commands.describe(
    titulo="Título do painel.",
    descricao="Instruções para o painel.",
    cargo1="Primeiro cargo vinculado.",
    cargo2="Segundo cargo (Opcional).",
    cargo3="Terceiro cargo (Opcional).",
    cargo4="Quarto cargo (Opcional).",
    cargo5="Quinto cargo (Opcional)."
)
async def painel_cargos_slash(
    interaction: discord.Interaction,
    titulo: str,
    descricao: str,
    cargo1: discord.Role,
    cargo2: discord.Role = None,
    cargo3: discord.Role = None,
    cargo4: discord.Role = None,
    cargo5: discord.Role = None
):
    """Cria o painel de cargos de forma nativa e persistente via Slash Command."""
    guild_id = str(interaction.guild.id) if interaction.guild else "DirectMessage"
    obter_ou_auto_registrar(interaction.user, guild_id)

    if not (interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id or interaction.user.id == DONO_BOT_ID):
        await interaction.response.send_message("❌ Apenas administradores podem criar painéis de cargos.", ephemeral=True)
        return

    roles_list = [cargo1]
    for c in [cargo2, cargo3, cargo4, cargo5]:
        if c:
            roles_list.append(c)

    embed = discord.Embed(title=titulo, description=descricao, color=discord.Color.blue())
    
    view = discord.ui.View(timeout=None)
    for r in roles_list:
        view.add_item(discord.ui.Button(
            label=r.name,
            style=discord.ButtonStyle.secondary,
            custom_id=f"role_{r.id}"
        ))

    await interaction.response.send_message(embed=embed, view=view)


# 2. GERENCIADOR DE FAQS / RESPOSTAS RÁPIDAS (#faq e /faq)
@bot.command(name="faq")
async def faq_prefix(ctx: commands.Context, sub_comando: str = None, chave: str = None, *, resposta: str = None):
    """Gerencia ou exibe respostas rápidas (FAQ) do servidor."""
    # Garante o auto-registro no primeiro comando usado
    obter_ou_auto_registrar(ctx.author, str(ctx.guild.id) if ctx.guild else "DirectMessage")

    guild_id = str(ctx.guild.id) if ctx.guild else "DirectMessage"
    
    if not sub_comando:
        await ctx.send("❌ **Como usar o FAQ:**\n"
                       "• `#faq <chave>` - Exibe a resposta salva para essa chave.\n"
                       "• `#faq add <chave> <resposta>` - Adiciona uma resposta rápida (Apenas Admins).\n"
                       "• `#faq del <chave>` - Remove uma resposta rápida (Apenas Admins).\n"
                       "• `#faq list` - Lista todas as respostas rápidas salvas.")
        return

    sub_clean = sub_comando.lower().strip()
    
    if sub_clean == "list":
        faqs = db_get(f"server_faqs/{guild_id}", {})
        if not faqs:
            await ctx.send("🔍 Nenhuma resposta rápida (FAQ) cadastrada neste servidor ainda.")
            return
        lista = [f"• `{k}`" for k in faqs.keys()]
        embed = discord.Embed(title="📚 Respostas Rápidas Cadastradas", description="\n".join(lista), color=discord.Color.blue())
        await ctx.send(embed=embed)
        return
        
    elif sub_clean == "add":
        if not (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID):
            await ctx.send("❌ Apenas administradores do servidor podem adicionar FAQs.")
            return
        if not chave or not resposta:
            await ctx.send("❌ **Sintaxe incorreta!** Use: `#faq add <chave> <sua resposta aqui>`")
            return
        
        db_set(f"server_faqs/{guild_id}/{chave.lower()}", resposta)
        await ctx.send(f"✅ **FAQ `{chave.lower()}` cadastrado com sucesso!**")
        return
        
    elif sub_clean in ["del", "delete"]:
        if not (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID):
            await ctx.send("❌ Apenas administradores do servidor podem remover FAQs.")
            return
        if not chave:
            await ctx.send("❌ **Sintaxe incorreta!** Use: `#faq del <chave>`")
            return
        
        db_set(f"server_faqs/{guild_id}/{chave.lower()}", None)
        await ctx.send(f"✅ **FAQ `{chave.lower()}` removido com sucesso!**")
        return
        
    else:
        # Se digitar apenas #faq <chave>
        chave_busca = sub_comando.lower()
        resposta_salva = db_get(f"server_faqs/{guild_id}/{chave_busca}")
        if resposta_salva:
            await ctx.send(resposta_salva)
        else:
            await ctx.send(f"❌ **FAQ não encontrado!** Não encontrei nenhuma resposta rápida para `{chave_busca}`. Digite `#faq list` para ver as disponíveis.")

@bot.tree.command(name="faq", description="Exibe ou gerencia respostas rápidas do servidor.")
@app_commands.describe(
    chave="A palavra-chave do FAQ a ser pesquisada ou alterada.",
    acao="Escolha 'add' (adicionar), 'del' (deletar) ou 'get' (obter, padrão).",
    resposta="A resposta para a chave (necessário apenas na ação de adicionar)."
)
async def faq_slash(interaction: discord.Interaction, chave: str, acao: str = "get", resposta: str = None):
    """Módulo de FAQ privado (ephemeral) para o Slash Command."""
    guild_id = str(interaction.guild.id) if interaction.guild else "DirectMessage"
    obter_ou_auto_registrar(interaction.user, guild_id)

    acao_clean = acao.lower().strip()
    chave_clean = chave.lower().strip()

    if acao_clean == "get":
        resposta_salva = db_get(f"server_faqs/{guild_id}/{chave_clean}")
        if resposta_salva:
            await interaction.response.send_message(resposta_salva)
        else:
            await interaction.response.send_message(f"❌ **FAQ não encontrado!** Nenhuma resposta rápida para `{chave_clean}`.", ephemeral=True)
            
    elif acao_clean == "add":
        if not (interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id or interaction.user.id == DONO_BOT_ID):
            await interaction.response.send_message("❌ Apenas administradores do servidor podem adicionar FAQs.", ephemeral=True)
            return
        if not resposta:
            await interaction.response.send_message("❌ **Parâmetro ausente!** Forneça uma resposta para a chave.", ephemeral=True)
            return
        
        db_set(f"server_faqs/{guild_id}/{chave_clean}", resposta)
        await interaction.response.send_message(f"✅ **FAQ `{chave_clean}` cadastrado com sucesso!**", ephemeral=True)
        
    elif acao_clean in ["del", "delete"]:
        if not (interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id or interaction.user.id == DONO_BOT_ID):
            await interaction.response.send_message("❌ Apenas administradores do servidor podem remover FAQs.", ephemeral=True)
            return
        
        db_set(f"server_faqs/{guild_id}/{chave_clean}", None)
        await interaction.response.send_message(f"✅ **FAQ `{chave_clean}` removido com sucesso!**", ephemeral=True)


# 3. ESCUDO ANTIRAID DE MONITORAMENTO DE SEGURANÇA (#monitorar, #desmonitorar, #monitorados)
@bot.command(name="monitorar")
async def monitorar_prefix(ctx: commands.Context, user_id: str = None):
    """Adiciona um ID do Discord à lista de monitoramento do Antiraid."""
    obter_ou_auto_registrar(ctx.author, str(ctx.guild.id) if ctx.guild else "DirectMessage")

    if not (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID):
        await ctx.send("❌ Apenas administradores podem configurar o monitoramento.")
        return
    if not user_id:
        await ctx.send("❌ Forneça o ID do Discord a ser monitorado.")
        return
        
    guild_id = str(ctx.guild.id)
    monitorados = db_get(f"server_config/{guild_id}/monitorados", [])
    if user_id in monitorados:
        await ctx.send("⚠️ Este ID já está na lista de monitoramento.")
        return
        
    monitorados.append(user_id)
    db_set(f"server_config/{guild_id}/monitorados", monitorados)
    await ctx.send(f"🛡️ **ID `{user_id}` adicionado à lista de monitoramento de segurança!** Se este usuário receber cargos administrativos, a ação será revertida e o responsável será punido.")

@bot.tree.command(name="monitorar", description="Adiciona um ID do Discord à lista de monitoramento de segurança.")
@app_commands.describe(user_id="O ID do Discord do usuário que deseja monitorar.")
async def monitorar_slash(interaction: discord.Interaction, user_id: str):
    """Monitora um ID de forma privada via Slash Command."""
    if not interaction.guild:
        await interaction.response.send_message("❌ Este comando só pode ser utilizado dentro de um servidor.", ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    obter_ou_auto_registrar(interaction.user, guild_id)

    if not (interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id or interaction.user.id == DONO_BOT_ID):
        await interaction.response.send_message("❌ Apenas administradores podem configurar o monitoramento.", ephemeral=True)
        return
        
    monitorados = db_get(f"server_config/{guild_id}/monitorados", [])
    if user_id in monitorados:
        await interaction.response.send_message("⚠️ Este ID já está na lista de monitoramento.", ephemeral=True)
        return
        
    monitorados.append(user_id)
    db_set(f"server_config/{guild_id}/monitorados", monitorados)
    await interaction.response.send_message(f"🛡️ **ID `{user_id}` adicionado à lista de monitoramento de segurança!** Se este usuário receber cargos administrativos, a ação será revertida e o responsável será punido.", ephemeral=True)

@bot.command(name="desmonitorar")
async def desmonitorar_prefix(ctx: commands.Context, user_id: str = None):
    """Remove um ID do Discord da lista de monitoramento."""
    obter_ou_auto_registrar(ctx.author, str(ctx.guild.id) if ctx.guild else "DirectMessage")

    if not (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID):
        await ctx.send("❌ Apenas administradores podem configurar o monitoramento.")
        return
    if not user_id:
        await ctx.send("❌ Forneça o ID do Discord a ser removido.")
        return
        
    guild_id = str(ctx.guild.id)
    monitorados = db_get(f"server_config/{guild_id}/monitorados", [])
    if user_id not in monitorados:
        await ctx.send("❌ Este ID não está na lista de monitoramento.")
        return
        
    monitorados.remove(user_id)
    db_set(f"server_config/{guild_id}/monitorados", monitorados)
    await ctx.send(f"✅ ID `{user_id}` removido da lista de monitoramento de segurança com sucesso.")

@bot.tree.command(name="desmonitorar", description="Remove um ID do Discord da lista de monitoramento de segurança.")
@app_commands.describe(user_id="O ID do Discord do usuário que deseja remover.")
async def desmonitorar_slash(interaction: discord.Interaction, user_id: str):
    """Remove um ID monitorado de forma privada."""
    if not interaction.guild:
        await interaction.response.send_message("❌ Este comando só pode ser utilizado dentro de um servidor.", ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    obter_ou_auto_registrar(interaction.user, guild_id)

    if not (interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id or interaction.user.id == DONO_BOT_ID):
        await interaction.response.send_message("❌ Apenas administradores podem configurar o monitoramento.", ephemeral=True)
        return
        
    monitorados = db_get(f"server_config/{guild_id}/monitorados", [])
    if user_id not in monitorados:
        await interaction.response.send_message("❌ Este ID não está na lista de monitoramento.", ephemeral=True)
        return
        
    monitorados.remove(user_id)
    db_set(f"server_config/{guild_id}/monitorados", monitorados)
    await interaction.response.send_message(f"✅ ID `{user_id}` removido do monitoramento com sucesso.", ephemeral=True)

@bot.command(name="monitorados")
async def monitorados_prefix(ctx: commands.Context):
    """Lista todos os IDs sob monitoramento de segurança neste servidor."""
    obter_ou_auto_registrar(ctx.author, str(ctx.guild.id) if ctx.guild else "DirectMessage")

    if not (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID):
        await ctx.send("❌ Apenas administradores podem ver a lista de monitorados.")
        return
        
    guild_id = str(ctx.guild.id)
    monitorados = db_get(f"server_config/{guild_id}/monitorados", [])
    if not monitorados:
        await ctx.send("🛡️ Nenhum usuário está sendo monitorado neste servidor atualmente.")
        return
        
    lista = [f"• `{uid}` (<@{uid}>)" for uid in monitorados]
    embed = discord.Embed(title="🛡️ IDs Sob Monitoramento de Segurança", description="\n".join(lista), color=discord.Color.red())
    await ctx.send(embed=embed)

@bot.tree.command(name="monitorados", description="Lista todos os IDs sob monitoramento de segurança neste servidor.")
async def monitorados_slash(interaction: discord.Interaction):
    """Lista monitorados de forma privada."""
    if not interaction.guild:
        await interaction.response.send_message("❌ Este comando só pode ser utilizado dentro de um servidor.", ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    obter_ou_auto_registrar(interaction.user, guild_id)

    if not (interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id or interaction.user.id == DONO_BOT_ID):
        await interaction.response.send_message("❌ Apenas administradores podem ver a lista de monitorados.", ephemeral=True)
        return
        
    guild_id = str(interaction.guild.id)
    monitorados = db_get(f"server_config/{guild_id}/monitorados", [])
    if not monitorados:
        await interaction.response.send_message("🛡️ Nenhum usuário está sendo monitorado neste servidor atualmente.", ephemeral=True)
        return
        
    lista = [f"• `{uid}` (<@{uid}>)" for uid in monitorados]
    embed = discord.Embed(title="🛡️ IDs Sob Monitoramento de Segurança", description="\n".join(lista), color=discord.Color.red())
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- EVENTO DE SEGURANÇA ATIVA: ON_MEMBER_UPDATE ---

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Detecta se um membro monitorado recebeu permissões administrativas, removendo o cargo dele e de quem o promoveu."""
    if after.id == bot.user.id:
        return
    
    guild_id = str(after.guild.id)
    monitorados = db_get(f"server_config/{guild_id}/monitorados", [])
    
    if not monitorados or str(after.id) not in [str(uid) for uid in monitorados if uid]:
        return
        
    antes_adm = False
    for r in before.roles:
        if r.permissions.manage_roles or r.permissions.manage_channels or r.permissions.administrator:
            antes_adm = True
            break
            
    agora_adm = False
    cargo_adicionado = None
    for r in after.roles:
        if r not in before.roles:
            if r.permissions.manage_roles or r.permissions.manage_channels or r.permissions.administrator:
                agora_adm = True
                cargo_adicionado = r
                break
                
    if not antes_adm and agora_adm and cargo_adicionado:
        logger.warning(f"ANTIRAID: Usuário monitorado {after.name} ({after.id}) recebeu o cargo: {cargo_adicionado.name}")
        
        try:
            await after.remove_roles(cargo_adicionado, reason="[ANTIRAID] Usuário sob monitoramento recebeu cargo de segurança!")
        except discord.Forbidden:
            logger.error(f"ANTIRAID: Falha de permissão ao remover cargo de {after.name}")

        quem_promoveu = None
        try:
            async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.member_role_update):
                if entry.target.id == after.id:
                    quem_promoveu = entry.user
                    break
        except Exception as e:
            logger.error(f"ANTIRAID: Falha ao ler logs de auditoria: {e}")
        
        if quem_promoveu and quem_promoveu.id == bot.user.id:
            logger.info(f"ANTIRAID: Promoção foi feita pelo bot (ignorado). User: {after.name}")
            return
            
        if quem_promoveu and isinstance(quem_promoveu, discord.Member):
            logger.info(f"ANTIRAID: Responsável identificado: {quem_promoveu.name} ({quem_promoveu.id})")
            
            try:
                for r_promotor in list(quem_promoveu.roles):
                    if r_promotor.permissions.manage_roles or r_promotor.permissions.manage_channels or r_promotor.permissions.administrator:
                        if r_promotor != after.guild.default_role:
                            try:
                                await quem_promoveu.remove_roles(r_promotor, reason="[ANTIRAID] Atribuiu cargo administrativo a um usuário monitorado!")
                            except discord.Forbidden:
                                pass
            except Exception as e:
                logger.error(f"ANTIRAID: Erro ao punir promotor {quem_promoveu.name}: {e}")

        canal_alerta = after.guild.system_channel
        if not canal_alerta:
            for ch in after.guild.text_channels:
                if ch.permissions_for(after.guild.me).send_messages:
                    canal_alerta = ch
                    break
                    
        if canal_alerta:
            embed = discord.Embed(
                title="🚨 INCIDENTE DE SEGURANÇA DETECTADO (ANTIRAID) 🚨",
                description=f"O usuário monitorado {after.mention} recebeu um cargo com permissões perigosas!",
                color=discord.Color.red()
            )
            embed.add_field(name="Membro Alvo", value=f"{after.mention} (Cargo `{cargo_adicionado.name}` Removido)", inline=True)
            if quem_promoveu:
                embed.add_field(name="Responsável Punido", value=f"{quem_promoveu.mention} (Cargos Administrativos Removidos)", inline=True)
            else:
                embed.add_field(name="Responsável", value="Não foi possível mapear nos logs a tempo", inline=True)
            await canal_alerta.send(embed=embed)


# --- COMANDO EXCLUSIVO: PAINEL ADMINISTRATIVO MASTER ---

@bot.command(name="painel-adm")
async def painel_adm_prefix(ctx: commands.Context):
    """Abre o painel interativo de administração master (Apenas para o dono do bot)."""
    if ctx.author.id != DONO_BOT_ID:
        await ctx.send("❌ **Acesso negado!** Apenas o dono do bot pode utilizar o painel administrativo.")
        return
        
    embed = discord.Embed(
        title="⚙️ Painel de Controle - Administração Master",
        description="Selecione uma das opções nos botões abaixo para gerenciar a nuvem e o status do bot:",
        color=discord.Color.dark_red()
    )
    view = PainelAdminView(bot, ctx.author.id)
    await ctx.send(embed=embed, view=view)

@bot.tree.command(name="painel-adm", description="Abre o painel interativo de administração master (Apenas para o dono do bot).")
async def painel_adm_slash(interaction: discord.Interaction):
    """Abre o painel de forma efêmera (privada) para o dono."""
    if interaction.user.id != DONO_BOT_ID:
        await interaction.response.send_message("❌ **Acesso negado!** Apenas o dono do bot pode usar este painel.", ephemeral=True)
        return
        
    embed = discord.Embed(
        title="⚙️ Painel de Controle - Administração Master",
        description="Selecione uma das opções nos botões abaixo para gerenciar a nuvem e o status do bot:",
        color=discord.Color.dark_red()
    )
    view = PainelAdminView(bot, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# --- INTERCEPTORES DE EVENTO PARA MÉTRICAS E AUTO-ROLE ---

@bot.before_invoke
async def monitorar_comandos_prefixo(ctx: commands.Context):
    """Registra uma atividade de comando sempre que um prefixado (#) for disparado."""
    registrar_execucao_comando()

@bot.event
async def on_interaction(interaction: discord.Interaction):
    """Registra estatísticas e intercepta de forma global cliques em botões persistentes de cargos."""
    if interaction.type == discord.InteractionType.application_command:
        registrar_execucao_comando()
        
    # Intercepta cliques de botões de cargos persistentes (role_ROLE_ID) [1]
    elif interaction.type == discord.InteractionType.component:
        custom_id = interaction.data.get("custom_id", "")
        if custom_id.startswith("role_"):
            role_id = int(custom_id.split("_")[1])
            guild = interaction.guild
            member = interaction.user
            role = guild.get_role(role_id) if guild else None
            
            if role:
                if role in member.roles:
                    try:
                        await member.remove_roles(role)
                        await interaction.response.send_message(f"✅ O cargo **{role.name}** foi removido de você!", ephemeral=True)
                    except discord.Forbidden:
                        await interaction.response.send_message("❌ Eu não tenho permissão hierárquica para remover este cargo de você. Coloque meu cargo acima dele na lista do Discord!", ephemeral=True)
                else:
                    try:
                        await member.add_roles(role)
                        await interaction.response.send_message(f"✅ O cargo **{role.name}** foi adicionado a você!", ephemeral=True)
                    except discord.Forbidden:
                        await interaction.response.send_message("❌ Eu não tenho permissão hierárquica para adicionar este cargo a você. Coloque meu cargo acima dele na lista do Discord!", ephemeral=True)


# --- COMANDOS: CENTRAL DE AJUDA (HELP / AJUDA) ---

def criar_embed_ajuda() -> discord.Embed:
    """Gera o Embed centralizado e organizado de ajuda para os usuários."""
    embed = discord.Embed(
        title="📚 Central de Ajuda - scn_bot",
        description="Aqui estão as instruções de uso e comandos disponíveis nesta nova versão do bot:",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="🔑 Registro Global (Auto-Registro)",
        value="`#registrar` ou `/registrar`\n"
              "• Cadastra o seu usuário no bot. O bot agora conta com **auto-registro invisível**: se você usar qualquer outro comando sem registro, o cadastro ocorre sozinho em segundo plano.",
        inline=False
    )
    embed.add_field(
        name="👤 Cartão de Perfil (Customizável)",
        value="`#perfil` ou `/perfil`\n"
              "• Desenha o seu cartão de perfil personalizado em formato de imagem com seu avatar redondo.\n"
              "`#perfil-config` ou `/perfil-config`\n"
              "• Customiza o visual do seu perfil (cor do fundo, imagem de fundo URL, biografia e posição da foto de perfil).",
        inline=False
    )
    embed.add_field(
        name="📋 Ficha de Servidores (Incremental)",
        value="`#registrar-servidor [@membro] [valores...]` ou `/registrar-servidor`\n"
              "• Registra fichas customizadas. Cada salvamento recebe uma **ID sequencial única** no servidor e grava quem o registrou (`owner`) por seu ID interno do bot.",
        inline=False
    )
    embed.add_field(
        name="⚙️ Configurações Administrativas",
        value="`#registrar-config` ou `/registrar-config`\n"
              "• Permite alterar a permissão de quem registra e customizar o nome dos rótulos de exibição (slots de 0 a 5) de forma livre.",
        inline=False
    )
    embed.add_field(
        name="📋 Respostas Rápidas (FAQ)",
        value="`#faq <chave>` ou `/faq`\n"
              "• Exibe ou gerencia (adiciona/remove) atalhos e links rápidos gravados na nuvem do Gist.",
        inline=False
    )
    embed.add_field(
        name="🛡️ Escudo de Segurança (Antiraid)",
        value="`#monitorar <id>` ou `/monitorar`\n"
              "• Adiciona um ID à lista de vigilância. Se ele receber cargos administrativos, o cargo será tirado dele e de quem o promoveu.\n"
              "`#desmonitorar <id>` / `#monitorados`\n"
              "• Gerencia e lista os IDs vigiados no servidor.",
        inline=False
    )
    embed.add_field(
        name="⚡ Utilitários Rápidos",
        value="`#ping` ou `/ping`\n"
              "• Mostra o tempo de latência de comunicação com a API do Discord em milissegundos.\n"
              "`#senha-adm` ou `/senha-adm`\n"
              "• Protocolo exclusivo de segurança do criador: Envia a senha master na DM e se auto-destrói em 5 segundos.\n"
              "`#painel-adm` ou `/painel-adm`\n"
              "• Abre o painel master com botões interativos para controle do sistema (Exclusivo do Dono).",
        inline=False
    )
    embed.set_footer(text="scn_bot - Nova Geração de Bots")
    return embed

@bot.command(name="help", aliases=["ajuda"])
async def help_prefix(ctx: commands.Context):
    """Menu de ajuda em prefixo (#help ou #ajuda)."""
    embed = criar_embed_ajuda()
    await ctx.send(embed=embed)

@bot.tree.command(name="help", description="Mostra o menu explicativo de ajuda com todos os comandos.")
async def help_slash(interaction: discord.Interaction):
    """Menu de ajuda em barra (/help)."""
    embed = criar_embed_ajuda()
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="ajuda", description="Mostra o menu explicativo de ajuda com todos os comandos.")
async def ajuda_slash(interaction: discord.Interaction):
    """Menu de ajuda em barra alternativo (/ajuda)."""
    embed = criar_embed_ajuda()
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- EVENTOS BÁSICOS DO DISCORD ---

@bot.event
async def on_ready():
    logger.info(f"Bot do Discord conectado com sucesso como {bot.user}")
    
    # Sincroniza o banco de dados do GitHub Gist com a memória RAM e backup local
    sincronizar_banco_local()
    
    try:
        synced = await bot.tree.sync()
        logger.info(f"Sincronizados {len(synced)} comandos de barra.")
    except Exception as e:
        logger.error(f"Erro ao sincronizar comandos de barra: {e}")


# --- INICIALIZADOR DO FLASK ---

def rodar_servidor_web():
    """Inicializa o servidor Flask associado à porta dinâmica do Render."""
    app.secret_key = "scn_bot_reimagined_master_key_123"
    porta = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=porta, debug=False, use_reloader=False)


# --- INICIALIZAÇÃO MULTI-THREADING ---

if __name__ == "__main__":
    # Dispara o Flask em segundo plano para que o Render consiga detectar o bind de porta instantaneamente
    thread_web = threading.Thread(target=rodar_servidor_web, daemon=True)
    thread_web.start()
    
    # Validação de segurança do Token do Discord
    if not TOKEN:
        logger.critical("DISCORD_TOKEN não foi encontrada nas variáveis de ambiente!")
        logger.info("Termux: export DISCORD_TOKEN='seu_token'")
        logger.info("Render: Adicione DISCORD_TOKEN na aba Environment Variables.")
    else:
        try:
            bot.run(TOKEN)
        except discord.errors.LoginFailure:
            logger.critical("Token do Discord inválido ou expirado.")
        except Exception as e:
            logger.critical(f"Erro crítico ao iniciar o bot: {e}")