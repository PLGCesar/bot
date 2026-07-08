import discord
from discord.ext import commands
from flask import Flask, request, render_template_string, redirect, session, url_for
import os
import threading

# --- CONFIGURAÇÃO DO BOT DO DISCORD ---
intents = discord.Intents.default()
# Deixado básico. Ative mais intents aqui no futuro conforme os novos comandos exigirem.
intents.message_content = True 

bot = commands.Bot(command_prefix="#", intents=intents, help_command=None)


# --- CONFIGURAÇÃO DO SERVIDOR WEB (FLASK) ---
app = Flask(__name__)
SENHA_ADMIN_FILE = "senha_admin.txt"


@app.route("/")
def index():
    # Redireciona a página principal diretamente para o painel de admin
    return redirect(url_for("admin"))


@app.route("/admin", methods=["GET", "POST"])
def admin():
    # Caso 1: Primeiro acesso (A senha ainda não existe no servidor)
    if not os.path.exists(SENHA_ADMIN_FILE):
        if request.method == "POST":
            senha_definida = request.form.get("senha")
            if senha_definida:
                with open(SENHA_ADMIN_FILE, "w", encoding="utf-8") as f:
                    f.write(senha_definida.strip())
                session["logado"] = True
                return redirect(url_for("admin"))
        
        # HTML do formulário de criação de senha (Primeiro Request de todos)
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

    # Caso 2: Senha já cadastrada, mas usuário não efetuou o login na sessão
    if not session.get("logado"):
        if request.method == "POST":
            senha_digitada = request.form.get("senha")
            try:
                with open(SENHA_ADMIN_FILE, "r", encoding="utf-8") as f:
                    senha_salva = f.read().strip()
            except Exception:
                senha_salva = ""
            
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
        
        # HTML da tela de Login padrão
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

    # Caso 3: Login efetuado com sucesso (Painel de Controle Ativo)
    bot_online = bot.is_ready()
    latencia = f"{bot.latency * 1000:.0f}ms" if bot_online else "N/A"
    servidores = len(bot.guilds) if bot_online else 0

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
                <div class="valor">{{ servidores }}</div>
            </div>
        </div>
        <a class="logout-btn" href="/logout">🚪 Sair do Painel</a>
    </body>
    </html>
    """, bot_online=bot_online, latencia=latencia, servidores=servidores)


@app.route("/logout")
def logout():
    session.pop("logado", None)
    return redirect(url_for("admin"))


# --- EVENTOS BÁSICOS DO DISCORD ---

@bot.event
async def on_ready():
    print(f"Bot do Discord conectado com sucesso como {bot.user}")


# --- INICIALIZADOR DO FLASK ---

def rodar_servidor_web():
    """Inicializa o servidor Flask com a porta dinâmica para o Render."""
    app.secret_key = "scn_bot_reimagined_master_key_123"
    porta = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=porta, debug=False, use_reloader=False)


# --- INICIALIZAÇÃO MULTI-THREADING ---

if __name__ == "__main__":
    # Inicializa o Flask em uma Thread separada (evita travar o bot)
    thread_web = threading.Thread(target=rodar_servidor_web, daemon=True)
    thread_web.start()
    
    # Inicializa o bot do Discord na Thread principal
    try:
        # Substitua pela sua chave (TOKEN) real do Discord
        bot.run (".")
    except discord.errors.LoginFailure:
        print("Erro: Token do bot inválido.")
    except Exception as e:
        print(f"Erro ao iniciar o bot: {e}")
