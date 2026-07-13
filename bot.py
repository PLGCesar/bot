import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask, request, render_template_string, redirect, session, url_for
import threading
import asyncio
import os

# Importações absolutas das ferramentas lógicas [3]
from templates_html import CONFIG_HTML, LOGIN_HTML, ADMIN_HTML
from database import (
    TOKEN, DONO_BOT_ID, SENHA_ADMIN_FILE, logger,
    sincronizar_banco_local, db_get, db_set, obter_registro,
    obter_ou_auto_registrar, obter_senha_admin, salvar_senha_admin, existe_senha_admin
)
from utils import (
    check_rate_limit, gerar_imagem_perfil, registrar_execucao_comando,
    obter_metricas_comandos, deserializar_permissoes_canal,
    extrair_estrutura_completa_servidor, TEMAS_DISPONIVEIS, obter_tema
)

# --- CONFIGURAÇÃO DO BOT DO DISCORD ---
intents = discord.Intents.default()
intents.message_content = True 
intents.members = True

bot = commands.Bot(command_prefix="#", intents=intents, help_command=None)

# --- CONFIGURAÇÃO DO SERVIDOR WEB (FLASK) ---
app = Flask(__name__)


# --- RECONSTRUÇÃO ASSÍNCRONA DE CLONER ---

async def reconstruir_servidor_completo(guild: discord.Guild, data: dict, canal_logs: discord.TextChannel):
    roles_data = data.get("roles", [])
    categories_data = data.get("categories", [])
    channels_data = data.get("channels", [])
    role_map = {}
    
    for r_info in roles_data:
        try:
            novo_cargo = await guild.create_role(
                name=r_info["name"], color=discord.Color(r_info["color"]),
                hoist=r_info["hoist"], mentionable=r_info["mentionable"],
                permissions=discord.Permissions(r_info["permissions"]), reason="[CLONER] Reconstruindo cargos"
            )
            role_map[str(r_info["id"])] = novo_cargo
            await asyncio.sleep(0.5)
        except Exception as e: logger.error(f"Erro ao criar cargo {r_info['name']}: {e}")
            
    category_map = {}
    for cat_info in categories_data:
        try:
            overwrites = deserializar_permissoes_canal(cat_info["overwrites"], guild, role_map)
            nova_cat = await guild.create_category(
                name=cat_info["name"], overwrites=overwrites,
                position=cat_info["position"], reason="[CLONER] Reconstruindo categorias"
            )
            category_map[str(cat_info["id"])] = nova_cat
            await asyncio.sleep(0.5)
        except Exception as e: logger.error(f"Erro ao criar categoria {cat_info['name']}: {e}")
            
    for ch_info in channels_data:
        try:
            overwrites = deserializar_permissoes_canal(ch_info["overwrites"], guild, role_map)
            cat_pai = None
            if ch_info["category_id"]:
                cat_id_str = str(ch_info["category_id"])
                if cat_id_str in category_map: cat_pai = category_map[cat_id_str]
                    
            if ch_info["type"] == "text":
                await guild.create_text_channel(
                    name=ch_info["name"], category=cat_pai, topic=ch_info["topic"],
                    overwrites=overwrites, position=ch_info["position"], reason="[CLONER] Reconstruindo canais"
                )
            elif ch_info["type"] == "voice":
                await guild.create_voice_channel(
                    name=ch_info["name"], category=cat_pai, overwrites=overwrites,
                    position=ch_info["position"], reason="[CLONER] Reconstruindo canais"
                )
            await asyncio.sleep(0.5)
        except Exception as e: logger.error(f"Erro ao criar canal {ch_info['name']}: {e}")
            
    try:
        embed = discord.Embed(title="✨ Reconstrução Concluída! ✨", description="Canais, categorias, cargos e permissões clonados.", color=discord.Color.green())
        await canal_logs.send(embed=embed)
    except Exception: pass


# --- VIEWS INTERATIVAS ---

class ColarConfirmacaoView(discord.ui.View):
    def __init__(self, bot_inst: commands.Bot, autor_id: int, template_dados: dict, guild_alvo: discord.Guild):
        super().__init__(timeout=60)
        self.bot = bot_inst
        self.autor_id = autor_id
        self.template_dados = template_dados
        self.guild_alvo = guild_alvo

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message("❌ Apenas o autor do comando pode autorizar.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirmar Reconstrução", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirmar_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("⚙️ **Reconstrução iniciada de forma assíncrona!**", ephemeral=True)
        for item in self.children: item.disabled = True
        await interaction.message.edit(view=self)
        asyncio.create_task(reconstruir_servidor_completo(self.guild_alvo, self.template_dados, interaction.channel))

class PainelAdminView(discord.ui.View):
    def __init__(self, bot_inst: commands.Bot, autor_id: int):
        super().__init__(timeout=180)
        self.bot = bot_inst
        self.autor_id = autor_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message("❌ **Acesso negado!** Apenas o dono pode interagir.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Status do Sistema", style=discord.ButtonStyle.primary, emoji="🖥️")
    async def status_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        bot_online = self.bot.is_ready()
        latencia = f"{self.bot.latency * 1000:.0f}ms" if bot_online else "N/A"
        servidores = len(self.bot.guilds)
        users_db = db_get("users", {})
        total_usuarios = len(users_db) if isinstance(users_db, dict) else 0
        next_id = db_get("global_config/proximo_bot_id", 1)
        
        embed = discord.Embed(title="🖥️ Status Detalhado", color=discord.Color.blue())
        embed.add_field(name="API", value="`ONLINE`" if bot_online else "`OFFLINE`", inline=True)
        embed.add_field(name="Ping", value=f"`{latencia}`", inline=True)
        embed.add_field(name="Guildas", value=f"`{servidores}`", inline=True)
        embed.add_field(name="Registros", value=f"`{total_usuarios}`", inline=True)
        embed.add_field(name="Próximo ID", value=f"`#{next_id}`", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Forçar Sync Gist", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def sync_gist_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            sincronizar_banco_local()
            await interaction.followup.send("✅ **Sync concluído!**", ephemeral=True)
        except Exception as e: await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

    @discord.ui.button(label="Baixar Backup", style=discord.ButtonStyle.secondary, emoji="📁")
    async def backup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if os.path.exists("local_db.json"):
            file = discord.File("local_db.json")
            await interaction.response.send_message(content="📄 Backup cache:", file=file, ephemeral=True)
        else: await interaction.response.send_message("❌ Nenhum backup localizado.", ephemeral=True)

    @discord.ui.button(label="Sincronizar Slash (/) ", style=discord.ButtonStyle.success, emoji="🔨")
    async def sync_slash_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            synced = await self.bot.tree.sync()
            await interaction.followup.send(f"✅ Sincronizados `{len(synced)}` slashes.", ephemeral=True)
        except Exception as e: await interaction.followup.send(f"❌ Erro sync: {e}", ephemeral=True)

    @discord.ui.button(label="Ver Registros", style=discord.ButtonStyle.primary, emoji="👥")
    async def ver_registros_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        users_db = db_get("users", {})
        if not users_db or not isinstance(users_db, dict):
            await interaction.response.send_message("🔍 Sem registros no banco.", ephemeral=True)
            return
        lista = [f"• ID Bot: `#{dados.get('bot_id', '?')}` | `{dados.get('nome', 'Desconhecido')}` (Discord: <@{d_id}>)" for d_id, dados in users_db.items()]
        texto_lista = "\n".join(lista[:15])
        if len(lista) > 15: texto_lista += f"\n*... e outros {len(lista) - 15} registros.*"
        embed = discord.Embed(title="👥 Usuários Cadastrados", description=texto_lista, color=discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)


# --- ROTAS FLASK COM HTML IMPORTADO ---

@app.route("/")
def index(): return redirect(url_for("admin"))

@app.route("/admin", methods=["GET", "POST"])
def admin():
    client_ip = request.remote_addr
    if not check_rate_limit(client_ip): return "⚠️ Muitas requisições. Aguarde.", 429
    
    if not existe_senha_admin():
        if request.method == "POST":
            senha_definida = request.form.get("senha")
            if senha_definida: salvar_senha_admin(senha_definida); session["logado"] = True; return redirect(url_for("admin"))
        return render_template_string(CONFIG_HTML)

    if not session.get("logado"):
        if request.method == "POST":
            if request.form.get("senha") == obter_senha_admin(): session["logado"] = True; return redirect(url_for("admin"))
            else: return '<div style="text-align:center;margin-top:50px;"><h3>❌ Incorreto</h3><a href="/admin">Voltar</a></div>'
        return render_template_string(LOGIN_HTML)

    bot_online = bot.is_ready()
    latencia = f"{bot.latency * 1000:.0f}ms" if bot_online else "N/A"
    servidores_count = len(bot.guilds) if bot_online else 0
    nomes_servidores = [g.name for g in bot.guilds] if bot_online else []
    qtd_comandos, media_comandos = obter_metricas_comandos()

    return render_template_string(ADMIN_HTML, bot_online=bot_online, latencia=latencia, servidores_count=servidores_count, nomes_servidores=nomes_servidores, qtd_comandos=qtd_comandos, media_comandos=media_comandos)

@app.route("/logout")
def logout(): session.pop("logado", None); return redirect(url_for("admin"))


# --- COMANDOS DO BOT (HÍBRIDOS) ---

@bot.command(name="ping")
async def ping_prefix(ctx: commands.Context):
    await ctx.send(f"🏓 **Pong!** `{round(bot.latency * 1000)}ms`.")

@bot.tree.command(name="ping", description="Verifica a latência atual do bot.")
async def ping_slash(interaction: discord.Interaction):
    await interaction.response.send_message(f"🎲 **Pong!** `{bot.latency * 1000:.0f}ms`.", ephemeral=True)

@bot.command(name="io-write")
async def io_write_prefix(ctx: commands.Context, *, texto: str = None):
    if not texto: await ctx.send("❌ Exemplo: `#io-write Teste`"); return
    if db_set("teste/mensagem", texto): await ctx.send(f"✅ Salvo:\n`{texto}`")
    else: await ctx.send("❌ Falha ao salvar.")

@bot.tree.command(name="io-write", description="Grava um texto de teste no banco de dados em nuvem.")
@app_commands.describe(texto="O texto que deseja salvar de forma persistente.")
async def io_write_slash(interaction: discord.Interaction, texto: str):
    if db_set("teste/mensagem", texto): await interaction.response.send_message(f"✅ Salvo:\n`{texto}`", ephemeral=True)
    else: await interaction.response.send_message("❌ Falha.", ephemeral=True)

@bot.command(name="io-read")
async def io_read_prefix(ctx: commands.Context):
    t = db_get("teste/mensagem")
    if t: await ctx.send(f"📖 Lido:\n`{t}`")
    else: await ctx.send("🔍 Nada salvo. Use #io-write.")

@bot.tree.command(name="io-read", description="Lê o texto de teste salvo no banco de dados em nuvem.")
async def io_read_slash(interaction: discord.Interaction):
    t = db_get("teste/mensagem")
    if t: await interaction.response.send_message(f"📖 Lido:\n`{t}`", ephemeral=True)
    else: await interaction.response.send_message("🔍 Vazio.", ephemeral=True)

@bot.command(name="registrar")
async def registrar_prefix(ctx: commands.Context):
    u = obter_ou_auto_registrar(ctx.author, str(ctx.guild.id) if ctx.guild else "DM")
    await ctx.send(f"🎉 Registrado! ID Bot: `#{u['bot_id']}`")

@bot.tree.command(name="registrar", description="Registra seu usuário globalmente no banco de dados do bot.")
async def registrar_slash(interaction: discord.Interaction):
    u = obter_ou_auto_registrar(interaction.user, str(interaction.guild.id) if interaction.guild else "DM")
    await interaction.response.send_message(f"🎉 Registrado! ID Bot: `#{u['bot_id']}`", ephemeral=True)

@bot.command(name="registrar-servidor")
async def registrar_servidor_prefix(ctx: commands.Context, membro: discord.Member = None, *, nomes_raw: str = None):
    if not ctx.guild or not membro: await ctx.send("❌ Erro. Uso: `#registrar-servidor @membro [nomes]`"); return
    guild_id = str(ctx.guild.id); cfg = db_get(f"server_config/{guild_id}", {})
    permissao = cfg.get("permissao_registrar_servidor", True)
    if not (ctx.author.id == DONO_BOT_ID or ctx.author.id == ctx.guild.owner_id or ctx.author.guild_permissions.administrator or permissao):
        await ctx.send("❌ Sem permissão."); return
    reg_id = cfg.get("proximo_registro_id", 1); cfg["proximo_registro_id"] = reg_id + 1; db_set(f"server_config/{guild_id}", cfg)
    lbls = cfg.get("labels", {"0":"Nome","1":"Nome1","2":"Nome2","3":"Nome3","4":"Nome4","5":"Nome5"})
    nomes = nomes_raw.split() if nomes_raw else []
    while len(nomes) < 6: nomes.append("Não Definido")
    nomes = nomes[:6]
    db_set(f"server_registrations/{guild_id}/{membro.id}", {"id": reg_id, "owner": obter_ou_auto_registrar(ctx.author, guild_id)["bot_id"], "server_id": guild_id, "owner_id": ctx.guild.owner_id, "registered_user_id": str(membro.id), "nome": nomes[0], "nome1": nomes[1], "nome2": nomes[2], "nome3": nomes[3], "nome4": nomes[4], "nome5": nomes[5]})
    embed = discord.Embed(title=f"📋 Ficha (ID: #{reg_id})!", description=f"Membro {membro.mention} registrado.", color=discord.Color.blue())
    for k, v in lbls.items(): embed.add_field(name=v, value=f"`{nomes[int(k)]}`", inline=True)
    await ctx.send(embed=embed)

@bot.tree.command(name="registrar-servidor", description="Cria uma ficha de registro personalizada para um membro no servidor.")
@app_commands.describe(membro="Membro", nome="Campo 0", nome1="Campo 1", nome2="Campo 2", nome3="Campo 3", nome4="Campo 4", nome5="Campo 5")
async def registrar_servidor_slash(interaction: discord.Interaction, membro: discord.Member, nome: str, nome1: str="Não Definido", nome2: str="Não Definido", nome3: str="Não Definido", nome4: str="Não Definido", nome5: str="Não Definido"):
    if not interaction.guild: await interaction.response.send_message("❌ Apenas em guilda.", ephemeral=True); return
    guild_id = str(interaction.guild.id); cfg = db_get(f"server_config/{guild_id}", {})
    permissao = cfg.get("permissao_registrar_servidor", True)
    if not (interaction.user.id == DONO_BOT_ID or interaction.user.id == interaction.guild.owner_id or interaction.user.guild_permissions.administrator or permissao):
        await interaction.response.send_message("❌ Sem permissão.", ephemeral=True); return
    reg_id = cfg.get("proximo_registro_id", 1); cfg["proximo_registro_id"] = reg_id + 1; db_set(f"server_config/{guild_id}", cfg)
    lbls = cfg.get("labels", {"0":"Nome","1":"Nome1","2":"Nome2","3":"Nome3","4":"Nome4","5":"Nome5"})
    db_set(f"server_registrations/{guild_id}/{membro.id}", {"id": reg_id, "owner": obter_ou_auto_registrar(interaction.user, guild_id)["bot_id"], "server_id": guild_id, "owner_id": interaction.guild.owner_id, "registered_user_id": str(membro.id), "nome": nome, "nome1": nome1, "nome2": nome2, "nome3": nome3, "nome4": nome4, "nome5": nome5})
    embed = discord.Embed(title=f"📋 Ficha (ID: #{reg_id})!", description=f"Membro {membro.mention} registrado.", color=discord.Color.blue())
    for k, v in lbls.items(): embed.add_field(name=v, value=f"`{locals()['nome' + k if k != '0' else 'nome']}`", inline=True)
    await interaction.response.send_message(embed=embed)

@bot.command(name="registrar-config")
async def registrar_config_prefix(ctx: commands.Context, sub_comando: str = None, *args):
    if not ctx.guild: await ctx.send("❌ Apenas em guilda."); return
    if not (ctx.author.id == DONO_BOT_ID or ctx.author.id == ctx.guild.owner_id or ctx.author.guild_permissions.administrator):
        await ctx.send("❌ Sem permissão."); return
    if not sub_comando: await ctx.send("• `#registrar-config <True/False>`\n• `#registrar-config label <0-5> <Novo Nome>`"); return
    guild_id = str(ctx.guild.id); cfg = db_get(f"server_config/{guild_id}", {})
    if sub_comando.lower() in ["true", "false", "sim", "nao", "não", "ativo", "ativado", "desativado", "inativo"]:
        cfg["permissao_registrar_servidor"] = sub_comando.lower() in ["true", "sim", "ativo", "ativado"]
        db_set(f"server_config/{guild_id}", cfg)
        await ctx.send(f"✅ Permissão geral atualizada para `{cfg['permissao_registrar_servidor']}`.")
    elif sub_comando.lower() == "label":
        if len(args) < 2 or not args[0].isdigit() or int(args[0]) < 0 or int(args[0]) > 5:
            await ctx.send("❌ Formato: `#registrar-config label [0-5] [Nome]`"); return
        lbls = cfg.get("labels", {})
        lbls[args[0]] = " ".join(args[1:])
        cfg["labels"] = lbls
        db_set(f"server_config/{guild_id}", cfg)
        await ctx.send(f"✅ Rótulo do Campo {args[0]} alterado para: `{lbls[args[0]]}`.")

@bot.tree.command(name="registrar-config", description="Altera as configurações de registro e customiza os campos do servidor.")
@app_commands.describe(permissao="True/False", campo_index="Campo 0-5", campo_nome="Novo nome do rótulo")
async def registrar_config_slash(interaction: discord.Interaction, permissao: bool = None, campo_index: int = None, campo_nome: str = None):
    if not interaction.guild: await interaction.response.send_message("❌ Apenas em guilda.", ephemeral=True); return
    if not (interaction.user.id == DONO_BOT_ID or interaction.user.id == interaction.guild.owner_id or interaction.user.guild_permissions.administrator):
        await interaction.response.send_message("❌ Sem permissão.", ephemeral=True); return
    guild_id = str(interaction.guild.id); cfg = db_get(f"server_config/{guild_id}", {})
    if permissao is not None: cfg["permissao_registrar_servidor"] = permissao
    if campo_index is not None and campo_nome is not None:
        if campo_index < 0 or campo_index > 5: await interaction.response.send_message("❌ Índice inválido.", ephemeral=True); return
        lbls = cfg.get("labels", {})
        lbls[str(campo_index)] = campo_nome
        cfg["labels"] = lbls
    db_set(f"server_config/{guild_id}", cfg)
    await interaction.response.send_message("✅ Configurações atualizadas!", ephemeral=True)

@bot.command(name="perfil")
async def perfil_prefix(ctx: commands.Context):
    u = obter_ou_auto_registrar(ctx.author, str(ctx.guild.id) if ctx.guild else "DM")
    t = obter_tema(u)
    async with ctx.typing():
        try:
            buf = await asyncio.get_running_loop().run_in_executor(None, gerar_imagem_perfil, u["nome"], u["bot_id"], ctx.author.display_avatar.with_format("png").url, u["perfil"]["avatar_pos"], u["perfil"]["descricao"], u["perfil"]["fundo"], u["perfil"]["fundo_url"], t)
            await ctx.send(file=discord.File(fp=buf, filename="perfil.png"))
        except Exception:
            embed = discord.Embed(title=f"👤 {u['nome']} (#{u['bot_id']})", description=u["perfil"]["descricao"], color=discord.Color.blue())
            await ctx.send("Incapaz de desenhar perfil, segue formato alternativo:", embed=embed)

@bot.tree.command(name="perfil", description="Exibe o seu cartão de perfil personalizado em formato de imagem.")
async def perfil_slash(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild.id) if interaction.guild else "DM"
    u = obter_ou_auto_registrar(interaction.user, guild_id)
    t = obter_tema(u)
    try:
        buf = await asyncio.get_running_loop().run_in_executor(None, gerar_imagem_perfil, u["nome"], u["bot_id"], interaction.user.display_avatar.with_format("png").url, u["perfil"]["avatar_pos"], u["perfil"]["descricao"], u["perfil"]["fundo"], u["perfil"]["fundo_url"], t)
        await interaction.followup.send(file=discord.File(fp=buf, filename="perfil.png"), ephemeral=True)
    except Exception:
        embed = discord.Embed(title=f"👤 {u['nome']} (#{u['bot_id']})", description=u["perfil"]["descricao"], color=discord.Color.blue())
        await interaction.followup.send(embed=embed, ephemeral=True)

@bot.command(name="perfil-tema")
async def perfil_tema_prefix(ctx: commands.Context, tema: str = None):
    if not tema:
        await ctx.send("🎨 Temas: `" + "`, `".join(TEMAS_DISPONIVEIS.keys()) + "`\nUse `#perfil-tema [nome]`.")
        return
    if tema not in TEMAS_DISPONIVEIS: await ctx.send("❌ Tema inválido."); return
    obter_ou_auto_registrar(ctx.author)
    db_set(f"users/{ctx.author.id}/tema", tema)
    await ctx.send(f"✅ Tema definido para `{TEMAS_DISPONIVEIS[tema]['nome']}`.")

@bot.tree.command(name="perfil-tema", description="Muda o tema visual do seu perfil.")
async def perfil_tema_slash(interaction: discord.Interaction, tema: str = None):
    if not tema:
        await interaction.response.send_message("🎨 Temas: `" + "`, `".join(TEMAS_DISPONIVEIS.keys()) + "`", ephemeral=True); return
    if tema not in TEMAS_DISPONIVEIS: await interaction.response.send_message("❌ Inválido.", ephemeral=True); return
    obter_ou_auto_registrar(interaction.user)
    db_set(f"users/{interaction.user.id}/tema", tema)
    await interaction.response.send_message(f"✅ Tema definido para `{TEMAS_DISPONIVEIS[tema]['nome']}`!", ephemeral=True)

@bot.command(name="perfil-config")
async def perfil_config_prefix(ctx: commands.Context, opcao: str = None, *, valor: str = None):
    u = obter_ou_auto_registrar(ctx.author); p = u["perfil"]
    if not opcao or not valor:
        await ctx.send("• `#perfil-config fundo <#Hex>`\n• `#perfil-config fundo_url <link>`\n• `#perfil-config posicao <se|sd|ie|id>`\n• `#perfil-config descricao <texto>`")
        return
    opcao = opcao.lower().strip()
    if opcao == "fundo": p["fundo"] = valor
    elif opcao == "fundo_url": p["fundo_url"] = valor
    elif opcao == "posicao":
        pmap = {"se":"superior_esquerdo","sd":"superior_direito","ie":"inferior_esquerdo","id":"inferior_direito"}
        p["avatar_pos"] = pmap.get(valor.lower().strip(), "superior_esquerdo")
    elif opcao in ["descricao", "desc"]: p["descricao"] = valor
    db_set(f"users/{ctx.author.id}/perfil", p)
    await ctx.send("✅ Perfil atualizado!")

@bot.tree.command(name="perfil-config", description="Personaliza os aspectos visuais e a biografia do seu cartão de perfil.")
@app_commands.describe(fundo="Hex", fundo_url="Link", posicao="se, sd, ie, id", descricao="Sua biografia")
async def perfil_config_slash(interaction: discord.Interaction, fundo: str = None, fundo_url: str = None, posicao: str = None, descricao: str = None):
    u = obter_ou_auto_registrar(interaction.user); p = u["perfil"]
    if fundo: p["fundo"] = fundo
    if fundo_url: p["fundo_url"] = fundo_url
    if posicao:
        pmap = {"se":"superior_esquerdo","sd":"superior_direito","ie":"inferior_esquerdo","id":"inferior_direito"}
        p["avatar_pos"] = pmap.get(posicao.lower().strip(), "superior_esquerdo")
    if descricao: p["descricao"] = descricao
    db_set(f"users/{interaction.user.id}/perfil", p)
    await interaction.response.send_message("✅ Perfil atualizado!", ephemeral=True)

@bot.command(name="senha-adm")
async def senha_adm_prefix(ctx: commands.Context):
    if ctx.author.id != DONO_BOT_ID: await ctx.send("❌ Apenas desenvolvedor master."); return
    s = obter_senha_admin() or "Acesse o painel para definir."
    try:
        dm = await ctx.author.create_dm()
        m = await dm.send(f"🔑 **Senha Master:** `{s}` *(Deleta em 5s)*")
        await ctx.send("✅ Senha enviada na DM.")
        await asyncio.sleep(5); await m.delete()
    except Exception as e: await ctx.send(f"❌ DM fechada. Erro: {e}")

@bot.tree.command(name="senha-adm", description="Envia a senha do painel do Flask na sua DM e a apaga após 5 segundos.")
async def senha_adm_slash(interaction: discord.Interaction):
    if interaction.user.id != DONO_BOT_ID: await interaction.response.send_message("❌ Dono apenas.", ephemeral=True); return
    s = obter_senha_admin() or "Acesse o painel."
    try:
        dm = await interaction.user.create_dm()
        m = await dm.send(f"🔑 **Senha Master:** `{s}` *(Deleta em 5s)*")
        await interaction.response.send_message("✅ Enviada.", ephemeral=True)
        await asyncio.sleep(5); await m.delete()
    except Exception as e: await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.command(name="painel-adm")
async def painel_adm_prefix(ctx: commands.Context):
    if ctx.author.id != DONO_BOT_ID: await ctx.send("❌ Dono apenas."); return
    await ctx.send(embed=discord.Embed(title="⚙️ Painel Master", description="Escolha uma opção:", color=discord.Color.dark_red()), view=PainelAdminView(bot, ctx.author.id))

@bot.tree.command(name="painel-adm", description="Abre o painel interativo de administração master (Apenas para o dono do bot).")
async def painel_adm_slash(interaction: discord.Interaction):
    if interaction.user.id != DONO_BOT_ID: await interaction.response.send_message("❌ Dono apenas.", ephemeral=True); return
    await interaction.response.send_message(embed=discord.Embed(title="⚙️ Painel Master", description="Escolha uma opção:", color=discord.Color.dark_red()), view=PainelAdminView(bot, interaction.user.id), ephemeral=True)

@bot.command(name="copiar")
async def copiar_prefix(ctx: commands.Context):
    if not ctx.guild: await ctx.send("❌ Apenas em guilda."); return
    if not (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID):
        await ctx.send("❌ Sem permissão."); return
    obter_ou_auto_registrar(ctx.author, str(ctx.guild.id))
    m = await ctx.send("⚙️ **Escaneando estrutura do servidor...**")
    try:
        d = extrair_estrutura_completa_servidor(ctx.guild); db_set(f"templates/{ctx.author.id}", d)
        embed = discord.Embed(title="✅ Copiado!", description="Estrutura de canais e permissões guardada na nuvem.", color=discord.Color.green())
        embed.add_field(name="Cargos", value=f"`{len(d['roles'])}`").add_field(name="Categorias", value=f"`{len(d['categories'])}`").add_field(name="Canais", value=f"`{len(d['channels'])}`")
        await m.delete(); await ctx.send(embed=embed)
    except Exception as e: await m.edit(content=f"❌ Erro ao clonar: {e}")

@bot.tree.command(name="copiar", description="Varre e copia canais, cargos, categorias e permissões para o seu clipboard em nuvem.")
async def copiar_slash(interaction: discord.Interaction):
    if not interaction.guild: await interaction.response.send_message("❌ Apenas em guilda.", ephemeral=True); return
    if not (interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id or interaction.user.id == DONO_BOT_ID):
        await interaction.response.send_message("❌ Sem permissão.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True); obter_ou_auto_registrar(interaction.user, str(interaction.guild.id))
    try:
        d = extrair_estrutura_completa_servidor(interaction.guild); db_set(f"templates/{interaction.user.id}", d)
        embed = discord.Embed(title="✅ Copiado!", description="Estrutura guardada de forma segura na nuvem.", color=discord.Color.green())
        embed.add_field(name="Cargos", value=f"`{len(d['roles'])}`").add_field(name="Categorias", value=f"`{len(d['categories'])}`").add_field(name="Canais", value=f"`{len(d['channels'])}`")
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e: await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

@bot.command(name="colar")
async def colar_prefix(ctx: commands.Context):
    if not ctx.guild: await ctx.send("❌ Apenas em guilda."); return
    if not (ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID): await ctx.send("❌ Apenas donos."); return
    d = db_get(f"templates/{ctx.author.id}")
    if not d: await ctx.send("❌ Clipboard vazio. Use #copiar antes."); return
    await ctx.send(embed=discord.Embed(title="⚠️ AVISO DE CLONAGEM ⚠️", description="Você criará cargos, canais e categorias salvos.\nExecute apenas em servidores vazios.\nDeseja iniciar?", color=discord.Color.orange()), view=ColarConfirmacaoView(bot, ctx.author.id, d, ctx.guild))

@bot.tree.command(name="colar", description="Reconstrói canais, cargos, categorias e permissões salvas no seu clipboard neste servidor.")
async def colar_slash(interaction: discord.Interaction):
    if not interaction.guild: await interaction.response.send_message("❌ Apenas em guilda.", ephemeral=True); return
    if not (interaction.user.id == interaction.guild.owner_id or interaction.user.id == DONO_BOT_ID): await interaction.response.send_message("❌ Apenas donos.", ephemeral=True); return
    d = db_get(f"templates/{interaction.user.id}")
    if not d: await interaction.response.send_message("❌ Clipboard vazio.", ephemeral=True); return
    await interaction.response.send_message(embed=discord.Embed(title="⚠️ AVISO DE CLONAGEM ⚠️", description="Você criará cargos, canais e categorias salvos.\nDeseja iniciar?", color=discord.Color.orange()), view=ColarConfirmacaoView(bot, interaction.user.id, d, interaction.guild), ephemeral=True)

@bot.command(name="monitorar")
async def monitorar_prefix(ctx: commands.Context, user_id: str = None):
    obter_ou_auto_registrar(ctx.author, str(ctx.guild.id) if ctx.guild else "DM")
    if not (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID):
        await ctx.send("❌ Apenas administradores."); return
    if not user_id: await ctx.send("❌ Forneça o ID."); return
    guild_id = str(ctx.guild.id); m = db_get(f"server_config/{guild_id}/monitorados", [])
    if user_id in m: await ctx.send("⚠️ Já monitorado."); return
    m.append(user_id); db_set(f"server_config/{guild_id}/monitorados", m)
    await ctx.send(f"🛡️ **ID `{user_id}` monitorado com sucesso!**")

@bot.tree.command(name="monitorar", description="Adiciona um ID do Discord à lista de monitoramento de segurança.")
@app_commands.describe(user_id="O ID do Discord do usuário que deseja monitorar.")
async def monitorar_slash(interaction: discord.Interaction, user_id: str):
    if not interaction.guild: await interaction.response.send_message("❌ Apenas em guilda.", ephemeral=True); return
    guild_id = str(interaction.guild.id); obter_ou_auto_registrar(interaction.user, guild_id)
    if not (interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id or interaction.user.id == DONO_BOT_ID):
        await interaction.response.send_message("❌ Apenas administradores.", ephemeral=True); return
    m = db_get(f"server_config/{guild_id}/monitorados", [])
    if user_id in m: await interaction.response.send_message("⚠️ Já monitorado.", ephemeral=True); return
    m.append(user_id); db_set(f"server_config/{guild_id}/monitorados", m)
    await interaction.response.send_message(f"🛡️ **ID `{user_id}` monitorado com sucesso!**", ephemeral=True)

@bot.command(name="desmonitorar")
async def desmonitorar_prefix(ctx: commands.Context, user_id: str = None):
    obter_ou_auto_registrar(ctx.author, str(ctx.guild.id) if ctx.guild else "DM")
    if not (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID):
        await ctx.send("❌ Apenas administradores."); return
    if not user_id: await ctx.send("❌ Forneça o ID."); return
    guild_id = str(ctx.guild.id); m = db_get(f"server_config/{guild_id}/monitorados", [])
    if user_id not in m: await ctx.send("❌ ID não monitorado."); return
    m.remove(user_id); db_set(f"server_config/{guild_id}/monitorados", m)
    await ctx.send(f"✅ ID `{user_id}` removido do monitoramento.")

@bot.tree.command(name="desmonitorar", description="Remove um ID do Discord da lista de monitoramento de segurança.")
@app_commands.describe(user_id="O ID do Discord do usuário que deseja remover.")
async def desmonitorar_slash(interaction: discord.Interaction, user_id: str):
    if not interaction.guild: await interaction.response.send_message("❌ Apenas em guilda.", ephemeral=True); return
    guild_id = str(interaction.guild.id); obter_ou_auto_registrar(interaction.user, guild_id)
    if not (interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id or interaction.user.id == DONO_BOT_ID):
        await interaction.response.send_message("❌ Apenas administradores.", ephemeral=True); return
    m = db_get(f"server_config/{guild_id}/monitorados", [])
    if user_id not in m: await interaction.response.send_message("❌ Não monitorado.", ephemeral=True); return
    m.remove(user_id); db_set(f"server_config/{guild_id}/monitorados", m)
    await interaction.response.send_message(f"✅ ID `{user_id}` removido.", ephemeral=True)

@bot.command(name="monitorados")
async def monitorados_prefix(ctx: commands.Context):
    obter_ou_auto_registrar(ctx.author, str(ctx.guild.id) if ctx.guild else "DM")
    if not (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID):
        await ctx.send("❌ Apenas administradores."); return
    guild_id = str(ctx.guild.id); m = db_get(f"server_config/{guild_id}/monitorados", [])
    if not m: await ctx.send("🛡️ Nenhum usuário monitorado."); return
    await ctx.send(embed=discord.Embed(title="🛡️ IDs Sob Vigilância", description="\n".join([f"• `{u}` (<@{uid}>)" for uid in m]), color=discord.Color.red()))

@bot.tree.command(name="monitorados", description="Lista todos os IDs sob monitoramento de segurança neste servidor.")
async def monitorados_slash(interaction: discord.Interaction):
    if not interaction.guild: await interaction.response.send_message("❌ Apenas em guilda.", ephemeral=True); return
    guild_id = str(interaction.guild.id); obter_ou_auto_registrar(interaction.user, guild_id)
    if not (interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id or interaction.user.id == DONO_BOT_ID):
        await interaction.response.send_message("❌ Apenas administradores.", ephemeral=True); return
    m = db_get(f"server_config/{guild_id}/monitorados", [])
    if not m: await interaction.response.send_message("🛡️ Nenhum usuário monitorado.", ephemeral=True); return
    await interaction.response.send_message(embed=discord.Embed(title="🛡️ IDs Sob Vigilância", description="\n".join([f"• `{uid}` (<@{uid}>)" for uid in m]), color=discord.Color.red()), ephemeral=True)


# --- INTERCEPTORES DE EVENTO ---

@bot.before_invoke
async def monitorar_comandos_prefixo(ctx: commands.Context):
    registrar_execucao_comando()

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.application_command:
        registrar_execucao_comando()
    elif interaction.type == discord.InteractionType.component:
        cid = interaction.data.get("custom_id", "")
        if cid.startswith("role_"):
            rid = int(cid.split("_")[1]); guild = interaction.guild; member = interaction.user; role = guild.get_role(rid) if guild else None
            if role:
                if role in member.roles:
                    try: await member.remove_roles(role); await interaction.response.send_message(f"✅ Cargo **{role.name}** removido!", ephemeral=True)
                    except discord.Forbidden: await interaction.response.send_message("❌ Falha de hierarquia. Coloque meu cargo acima dele!", ephemeral=True)
                else:
                    try: await member.add_roles(role); await interaction.response.send_message(f"✅ Cargo **{role.name}** adicionado!", ephemeral=True)
                    except discord.Forbidden: await interaction.response.send_message("❌ Falha de hierarquia. Coloque meu cargo acima dele!", ephemeral=True)


# --- EVENTO DE SEGURANÇA ATIVA: ON_MEMBER_UPDATE ---

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if after.id == bot.user.id: return
    guild_id = str(after.guild.id); monitorados = db_get(f"server_config/{guild_id}/monitorados", [])
    if not monitorados or str(after.id) not in [str(uid) for uid in monitorados if uid]: return
    
    antes_adm = any(r.permissions.manage_roles or r.permissions.manage_channels or r.permissions.administrator for r in before.roles)
    agora_adm = False; cargo_add = None
    for r in after.roles:
        if r not in before.roles and (r.permissions.manage_roles or r.permissions.manage_channels or r.permissions.administrator):
            agora_adm = True; cargo_add = r; break
            
    if not antes_adm and agora_adm and cargo_add:
        logger.warning(f"ANTIRAID: {after.name} ({after.id}) recebeu o cargo perigoso: {cargo_add.name}")
        try: await after.remove_roles(cargo_add, reason="[ANTIRAID] Usuário vigiado recebeu cargo administrativo!")
        except discord.Forbidden: logger.error(f"ANTIRAID: Falha de permissão ao punir {after.name}")
        
        promotor = None
        try:
            async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.member_role_update):
                if entry.target.id == after.id: promotor = entry.user; break
        except Exception as e: logger.error(f"ANTIRAID: Falha de auditoria: {e}")
        
        if promotor and promotor.id == bot.user.id: return
        
        if promotor and isinstance(promotor, discord.Member):
            logger.info(f"ANTIRAID: Promotor identificado: {promotor.name} ({promotor.id})")
            try:
                for rp in list(promotor.roles):
                    if (rp.permissions.manage_roles or rp.permissions.manage_channels or rp.permissions.administrator) and rp != after.guild.default_role:
                        try: await promotor.remove_roles(rp, reason="[ANTIRAID] Deu cargo a um usuário sob vigilância!")
                        except discord.Forbidden: pass
            except Exception as e: logger.error(f"ANTIRAID: Erro ao punir promotor {promotor.name}: {e}")
            
        canal = after.guild.system_channel
        if not canal:
            for ch in after.guild.text_channels:
                if ch.permissions_for(after.guild.me).send_messages: canal = ch; break
        if canal:
            embed = discord.Embed(title="🚨 INCIDENTE DE SEGURANÇA DETECTADO (ANTIRAID) 🚨", description=f"O usuário monitorado {after.mention} recebeu um cargo com permissões perigosas!", color=discord.Color.red())
            embed.add_field(name="Membro Alvo", value=f"{after.mention} (Cargo `{cargo_add.name}` Removido)", inline=True)
            embed.add_field(name="Responsável Punido", value=f"{promotor.mention if promotor else 'Desconhecido'} (Cargos Removidos)", inline=True)
            await canal.send(embed=embed)


# --- AJUDA DO BOT (HELP) ---

def criar_embed_ajuda() -> discord.Embed:
    embed = discord.Embed(title="📚 Central de Ajuda - scn_bot", description="Comandos disponíveis no bot:", color=discord.Color.blue())
    embed.add_field(name="🔑 Registro (Auto)", value="• `#registrar` / `/registrar`:\nRegistra seu perfil (Invisível nos outros comandos).", inline=False)
    embed.add_field(name="👤 Perfil (Imagens)", value="• `#perfil` / `/perfil`:\nDesenha seu cartão de perfil personalizado em imagem.\n• `#perfil-config` / `/perfil-config`:\nCustomiza fundo (cor/link), bio e avatar do perfil.\n• `#perfil-tema` / `/perfil-tema`:\nEscolha temas (Ocean, Forest, Neon, Sunset, Light, Dark).", inline=False)
    embed.add_field(name="📋 Ficha de Servidores", value="• `#registrar-servidor [@membro] [valores]` / `/registrar-servidor`:\nRegistra fichas de membros. Cada salvamento possui ID próprio.", inline=False)
    embed.add_field(name="⚙️ Configurações", value="• `#registrar-config` / `/registrar-config`:\nDefine permissões públicas ou customiza o nome dos slots de ficha (0-5).", inline=False)
    embed.add_field(name="📋 Respostas Rápidas (FAQ)", value="• `#faq <chave>` / `/faq`:\nExibe ou gerencia respostas rápidas gravadas na nuvem.", inline=False)
    embed.add_field(name="🛡️ Antiraid (Vigilância)", value="• `#monitorar <id>` / `/monitorar`:\nAdiciona um ID à lista de segurança de cargos perigosos.\n• `#desmonitorar` / `#monitorados`:\nGerencia a lista de segurança.", inline=False)
    embed.add_field(name="🎛️ Clonagem (Cloner)", value="• `#copiar` / `/copiar`:\nSalva cargos, categorias, canais e permissões na nuvem.\n• `#colar` / `/colar`:\nReconstrói a estrutura física salva neste servidor.", inline=False)
    embed.add_field(name="⚡ Utilitários", value="• `#ping` / `/ping`: Latência atual.\n• `#senha-adm` / `/senha-adm`: Envia senha master na DM (apaga em 5s).\n• `#painel-adm` / `/painel-adm`: Painel de controle Master de botões.", inline=False)
    embed.set_footer(text="scn_bot - Nova Geração")
    return embed

@bot.command(name="help", aliases=["ajuda"])
async def help_prefix(ctx: commands.Context): await ctx.send(embed=criar_embed_ajuda())

@bot.tree.command(name="help", description="Mostra o menu explicativo de ajuda com todos os comandos.")
async def help_slash(interaction: discord.Interaction): await interaction.response.send_message(embed=criar_embed_ajuda(), ephemeral=True)

@bot.tree.command(name="ajuda", description="Mostra o menu explicativo de ajuda com todos os comandos.")
async def ajuda_slash(interaction: discord.Interaction): await interaction.response.send_message(embed=criar_embed_ajuda(), ephemeral=True)


# --- EVENTO ON_READY ---

@bot.event
async def on_ready():
    logger.info(f"Bot conectado como {bot.user}")
    sincronizar_banco_local()
    try:
        synced = await bot.tree.sync()
        logger.info(f"Sincronizados {len(synced)} slashes.")
    except Exception as e: logger.error(f"Erro sync: {e}")

# --- INICIALIZAÇÃO WEB ---

def rodar_servidor_web():
    app.secret_key = "scn_bot_reimagined_master_key_123"
    porta = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=porta, debug=False, use_reloader=False)

if __name__ == "__main__":
    threading.Thread(target=rodar_servidor_web, daemon=True).start()
    if not TOKEN: logger.critical("DISCORD_TOKEN ausente nas variáveis de ambiente.")
    else:
        try: bot.run(TOKEN)
        except discord.errors.LoginFailure: logger.critical("Token inválido.")
        except Exception as e: logger.critical(f"Erro crítico de boot: {e}")