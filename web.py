from flask import Flask, request, render_template_string, redirect, session, url_for
import os
from templates_html import CONFIG_HTML, LOGIN_HTML, ADMIN_HTML
from database import obter_senha_admin, salvar_senha_admin, existe_senha_admin, db_get
from utils import check_rate_limit, obter_metricas_comandos, logger

app = Flask(__name__)

# Será injetado pelo bot.py no startup para evitar importação circular
bot_ref = None 

@app.route("/")
def index():
    return redirect(url_for("admin"))

@app.route("/admin", methods=["GET", "POST"])
def admin():
    client_ip = request.remote_addr
    if not check_rate_limit(client_ip):
        return "⚠️ Muitas requisições. Tente novamente em alguns minutos.", 429
    
    if not existe_senha_admin():
        if request.method == "POST":
            senha_definida = request.form.get("senha")
            if senha_definida:
                salvar_senha_admin(senha_definida)
                session["logado"] = True
                return redirect(url_for("admin"))
        return render_template_string(CONFIG_HTML)

    if not session.get("logado"):
        if request.method == "POST":
            if request.form.get("senha") == obter_senha_admin():
                session["logado"] = True
                return redirect(url_for("admin"))
            else:
                return '<div style="text-align:center;margin-top:50px;"><h3>❌ Incorreto</h3><a href="/admin">Voltar</a></div>'
        return render_template_string(LOGIN_HTML)

    # Usa a referência do bot injetada dinamicamente
    bot_online = bot_ref.is_ready() if bot_ref else False
    latencia = f"{bot_ref.latency * 1000:.0f}ms" if bot_online else "N/A"
    servidores_count = len(bot_ref.guilds) if bot_online else 0
    nomes_servidores = [g.name for g in bot_ref.guilds] if bot_online else []
    qtd_comandos, media_comandos = obter_metricas_comandos()

    return render_template_string(
        ADMIN_HTML, bot_online=bot_online, latencia=latencia, 
        servidores_count=servidores_count, nomes_servidores=nomes_servidores, 
        qtd_comandos=qtd_comandos, media_comandos=media_comandos
    )

@app.route("/logout")
def logout():
    session.pop("logado", None)
    return redirect(url_for("admin"))

def rodar_servidor_web():
    """Inicializa o servidor Flask associado à porta dinâmica do Render."""
    app.secret_key = "scn_bot_reimagined_master_key_123"
    porta = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=porta, debug=False, use_reloader=False)