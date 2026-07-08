import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask, request, render_template_string, redirect, session, url_for
import os
import json
import threading
import asyncio
import time
import firebase_admin
from firebase_admin import credentials, db

# --- VARIÁVEIS DE AMBIENTE (Puxadas do Render ou Termux) ---
TOKEN = os.environ.get('DISCORD_TOKEN')
FIREBASE_URL = os.environ.get('FIREBASE_DATABASE_URL')
FIREBASE_CREDS = os.environ.get('FIREBASE_CREDENTIALS')

# ID do Dono do Bot
DONO_BOT_ID = 1520539744457461892

# --- CONFIGURAÇÃO DO BOT DO DISCORD ---
intents = discord.Intents.default()
intents.message_content = True 

bot = commands.Bot(command_prefix="#", intents=intents, help_command=None)

# --- CONFIGURAÇÃO DO SERVIDOR WEB (FLASK) ---
app = Flask(__name__)
SENHA_ADMIN_FILE = "senha_admin.txt"
COMANDOS_TMP_FILE = "comandos_hora.tmp"


# --- INICIALIZAÇÃO SEGURA DO FIREBASE ---
firebase_ativo = False

if FIREBASE_URL and FIREBASE_CREDS:
    try:
        creds_dict = json.loads(FIREBASE_CREDS)
        cred = credentials.Certificate(creds_dict)
        firebase_admin.initialize_app(cred, {
            'databaseURL': FIREBASE_URL
        })
        firebase_ativo = True
        print("[Firebase] Conectado com sucesso utilizando as credenciais fornecidas!")
    except Exception as e:
        print(f"[Erro Firebase] Falha de sintaxe ou conexão com as credenciais: {e}")
else:
    print("[Aviso] Firebase não detectado nas variáveis de ambiente. Usando fallback local temporário...")


# --- ENGENHARIA DE ACESSO ABSTRATO AO BANCO DE DADOS (DYNAMO HYBRID) ---

def db_get(path: str, default=None):
    """Busca dados no Firebase ou foca no arquivo local JSON caso o Firebase esteja inativo."""
    if firebase_ativo:
        try:
            ref = db.reference(path)
            dados = ref.get()
            if dados is not None:
                return dados
        except Exception as e:
            print(f"[Erro Leitura Firebase] {e}")

    # Fallback para JSON local (Perfeito para testar no Termux)
    local_db_file = "local_db.json"
    if os.path.exists(local_db_file):
        try:
            with open(local_db_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                keys = path.strip("/").split("/")
                temp = data
                for k in keys:
                    temp = temp[k]
                return temp
        except Exception:
            pass
    return default

def db_set(path: str, value) -> bool:
    """Grava dados de forma persistente no Firebase ou no arquivo local JSON."""
    if firebase_ativo:
        try:
            ref = db.reference(path)
            ref.set(value)
            return True
        except Exception as e:
            print(f"[Erro Escrita Firebase] {e}")
            
    # Fallback para JSON local (Termux)
    local_db_file = "local_db.json"
    data = {}
    if os.path.exists(local_db_file):
        try:
            with open(local_db_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
            
    keys = path.strip("/").split("/")
    temp = data
    for k in keys[:-1]:
        if k not in temp or not isinstance(temp[k], dict):
            temp[k] = {}
        temp = temp[k]
    temp[keys[-1]] = value
    
    try:
        with open(local_db_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[Erro Escrita Local] Falha ao salvar no local_db.json: {e}")
    return False


# --- FUNÇÕES DE PERSISTÊNCIA DA SENHA DO FLASK ---

def obter_senha_admin() -> str:
    """Busca a senha master gravada."""
    return db_get("admin_config/password", "")

def salvar_senha_admin(senha: str) -> bool:
    """Grava a senha master permanentemente."""
    try:
        with open(SENHA_ADMIN_FILE, "w", encoding="utf-8") as f:
            f.write(senha.strip())
    except Exception:
        pass
    return db_set("admin_config/password", senha.strip())

def existe_senha_admin() -> bool:
    """Verifica se já existe uma senha configurada no banco."""
    return len(obter_senha_admin()) > 0


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
        print(f"[Erro Métricas] Falha ao gravar comandos_hora.tmp: {e}")

def obter_metricas_comandos() -> tuple:
    """Retorna a quantidade acumulada e a média de comandos por hora (Quantidade / 2)."""
    if not os.path.exists(COMANDOS_TMP_FILE):
        return 0, 0.0
        
    try:
        with open(COMANDOS_TMP_FILE, "r", encoding="utf-8") as f:
            dados = json.load(f)
            
        agora = time.time()
        # Se passou de 1 hora desde a primeira gravação, o arquivo está expirado [2]
        if agora - dados.get("timestamp_inicial", agora) > 3600:
            try:
                os.remove(COMANDOS_TMP_FILE)
            except Exception:
                pass
            return 0, 0.0
            
        quantidade = dados.get("quantidade", 0)
        # Média conceito: quantidade dividida por 2
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
                    {% for nome in nomes_servidores %}
                        <li>{{ nome }}</li>
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


# --- COMANDOS: UTILS ---

# Comando de Ping Híbrido (#ping e /ping)
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

    # Processamento de novos registros
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
    
    # Busca configurações de permissão do servidor no banco de dados
    server_config = db_get(f"server_config/{guild_id}", {"permissao_registrar_servidor": True})
    permissao_geral = server_config.get("permissao_registrar_servidor", True)
    
    # Controle de permissões (Dono do bot, dono do servidor ou Administradores do Discord)
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

    # Organiza e fatiamento dos argumentos (Até 6 nomes suportados)
    nomes = nomes_raw.split() if nomes_raw else []
    while len(nomes) < 6:
        nomes.append("Não Definido")
    nomes = nomes[:6]

    registro_membro = {
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
        title="📋 Ficha de Servidor Registrada!",
        description=f"O membro {membro.mention} recebeu uma ficha de registro associada a este servidor.",
        color=discord.Color.blue()
    )
    embed.add_field(name="Nome Principal", value=f"`{nomes[0]}`", inline=True)
    embed.add_field(name="Nome 1", value=f"`{nomes[1]}`", inline=True)
    embed.add_field(name="Nome 2", value=f"`{nomes[2]}`", inline=True)
    embed.add_field(name="Nome 3", value=f"`{nomes[3]}`", inline=True)
    embed.add_field(name="Nome 4", value=f"`{nomes[4]}`", inline=True)
    embed.add_field(name="Nome 5", value=f"`{nomes[5]}`", inline=True)
    
    await ctx.send(embed=embed)

@bot.tree.command(name="registrar-servidor", description="Cria uma ficha de registro para um membro no servidor.")
@app_commands.describe(
    membro="Membro a ser registrado.",
    nome="Nome principal customizado.",
    nome1="Campo 1 customizado.",
    nome2="Campo 2 customizado.",
    nome3="Campo 3 customizado.",
    nome4="Campo 4 customizado.",
    nome5="Campo 5 customizado."
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
    server_config = db_get(f"server_config/{guild_id}", {"permissao_registrar_servidor": True})
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
    embed.add_field(name="Nome Principal", value=f"`{nome}`", inline=True)
    embed.add_field(name="Nome 1", value=f"`{nome1}`", inline=True)
    embed.add_field(name="Nome 2", value=f"`{nome2}`", inline=True)
    embed.add_field(name="Nome 3", value=f"`{nome3}`", inline=True)
    embed.add_field(name="Nome 4", value=f"`{nome4}`", inline=True)
    embed.add_field(name="Nome 5", value=f"`{nome5}`", inline=True)
    
    await interaction.response.send_message(embed=embed)


# 3. COMANDO #registrar-config / /registrar-config
@bot.command(name="registrar-config")
async def registrar_config_prefix(ctx: commands.Context, valor: str = None):
    if not ctx.guild:
        await ctx.send("❌ Este comando só pode ser utilizado dentro de um servidor.")
        return

    guild_id = str(ctx.guild.id)
    
    # Verificação de permissões do editor
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

    if not valor:
        await ctx.send("❌ **Parâmetro em falta!** Escreva `True` para ativar a permissão pública ou `False` para restringi-la.\nExemplo: `#registrar-config False`")
        return

    # Normalização segura do input booleano
    val_bool = None
    if valor.lower() in ["true", "sim", "yes", "ativo", "ativado"]:
        val_bool = True
    elif valor.lower() in ["false", "nao", "não", "inativo", "desativado"]:
        val_bool = False

    if val_bool is None:
        await ctx.send("❌ **Valor inválido!** Especifique `True` (Permitir Todos) ou `False` (Apenas Admins).")
        return

    # Altera e salva o JSON no banco de dados
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
    
    if ctx.author.id == DONO_BOT_ID:
        embed.add_field(
            name="👑 Desenvolvedor Autorizado",
            value="Seu ID sênior foi reconhecido. Você pode solicitar o envio da senha master do painel web utilizando `#senha-adm`.",
            inline=False
        )

    await ctx.send(embed=embed)

@bot.tree.command(name="registrar-config", description="Define quem pode registrar membros neste servidor.")
@app_commands.describe(permissao="Defina como True (Qualquer um registra) ou False (Apenas administradores podem registrar).")
async def registrar_config_slash(interaction: discord.Interaction, permissao: bool):
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
    server_config["permissao_registrar_servidor"] = permissao
    db_set(f"server_config/{guild_id}", server_config)

    status_txt = "PÚBLICO (Qualquer membro pode utilizar o comando)" if permissao else "RESTRITO (Apenas administradores e donos podem registrar)"
    
    embed = discord.Embed(
        title="⚙️ Configurações do Servidor Atualizadas!",
        description="A política de controle de registro de membros foi alterada de forma privada.",
        color=discord.Color.gold()
    )
    embed.add_field(name="Permissão Geral", value=f"`{status_txt}`", inline=False)
    
    if interaction.user.id == DONO_BOT_ID:
        embed.add_field(
            name="👑 Desenvolvedor Autorizado",
            value="Seu ID sênior foi reconhecido. Você pode obter a chave master utilizando `/senha-adm`.",
            inline=False
        )

    await interaction.response.send_message(embed=embed)


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
        
        # Aguarda 5 segundos antes de apagar a mensagem na DM
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


# --- INTERCEPTORES DE EVENTO PARA MÉTRICAS (MÉDIA DE USO) ---

@bot.before_invoke
async def monitorar_comandos_prefixo(ctx: commands.Context):
    """Registra uma atividade de comando sempre que um prefixado (#) for disparado."""
    registrar_execucao_comando()

@bot.event
async def on_interaction(interaction: discord.Interaction):
    """Registra uma atividade de comando sempre que uma interação (/ ou botões) for ativada."""
    if interaction.type == discord.InteractionType.application_command:
        registrar_execucao_comando()
    # Encaminha a interação para o tratamento nativo do discord.py
    await bot.process_application_commands(interaction)


# --- EVENTOS BÁSICOS DO DISCORD ---

@bot.event
async def on_ready():
    print(f"Bot do Discord conectado com sucesso como {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Sincronizados {len(synced)} comandos de barra.")
    except Exception as e:
        print(f"Erro ao sincronizar comandos de barra: {e}")


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
        print("\n" + "="*60)
        print("❌ ERRO: A variável de ambiente 'DISCORD_TOKEN' não foi encontrada!")
        print("\n• Se estiver rodando no Termux, use: export DISCORD_TOKEN='seu_token'")
        print("• Se estiver rodando no Render, adicione 'DISCORD_TOKEN' na aba Environment Variables.")
        print("="*60 + "\n")
    else:
        try:
            bot.run(TOKEN)
        except discord.errors.LoginFailure:
            print("Erro: O Token do bot fornecido na variável de ambiente é inválido.")
        except Exception as e:
            print(f"Erro ao iniciar o bot: {e}")
