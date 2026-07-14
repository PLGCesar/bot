import discord
from discord.ext import commands
from discord import app_commands
import asyncio, os

from database import DONO_BOT_ID, db_get, db_set, obter_senha_admin, logger
from utils import check_rate_limit, registrar_execucao_comando, obter_metricas_comandos, deserializar_permissoes_canal, extrair_estrutura_completa_servidor, rolar_dado_viciado

async def reconstruir_servidor_completo(guild: discord.Guild, data: dict, canal_logs: discord.TextChannel):
    roles_data = data.get("roles", []); categories_data = data.get("categories", []); channels_data = data.get("channels", []); role_map = {}
    for r_info in roles_data:
        try:
            role_map[str(r_info["id"])] = await guild.create_role(name=r_info["name"], color=discord.Color(r_info["color"]), hoist=r_info["hoist"], mentionable=r_info["mentionable"], permissions=discord.Permissions(r_info["permissions"]), reason="[CLONER] cargos")
            await asyncio.sleep(0.5)
        except Exception as e: logger.error(f"Erro cargo {r_info['name']}: {e}")
    category_map = {}
    for cat_info in categories_data:
        try:
            category_map[str(cat_info["id"])] = await guild.create_category(name=cat_info["name"], overwrites=deserializar_permissoes_canal(cat_info["overwrites"], guild, role_map), position=cat_info["position"], reason="[CLONER] categorias")
            await asyncio.sleep(0.5)
        except Exception as e: logger.error(f"Erro cat {cat_info['name']}: {e}")
    for ch_info in channels_data:
        try:
            ow = deserializar_permissoes_canal(ch_info["overwrites"], guild, role_map); cat = category_map.get(str(ch_info["category_id"])) if ch_info["category_id"] else None
            if ch_info["type"] == "text": await guild.create_text_channel(name=ch_info["name"], category=cat, topic=ch_info["topic"], overwrites=ow, position=ch_info["position"], reason="[CLONER] canais")
            elif ch_info["type"] == "voice": await guild.create_voice_channel(name=ch_info["name"], category=cat, overwrites=ow, position=ch_info["position"], reason="[CLONER] canais")
            await asyncio.sleep(0.5)
        except Exception as e: logger.error(f"Erro canal {ch_info['name']}: {e}")
    try: await canal_logs.send(embed=discord.Embed(title="✨ Reconstrução Concluída! ✨", color=discord.Color.green()))
    except Exception: pass

class ColarConfirmacaoView(discord.ui.View):
    def __init__(self, bot_inst, autor_id: int, template_dados: dict, guild_alvo):
        super().__init__(timeout=60); self.bot = bot_inst; self.autor_id = autor_id; self.template_dados = template_dados; self.guild_alvo = guild_alvo
    async def interaction_check(self, inter) -> bool:
        if inter.user.id != self.autor_id: await inter.response.send_message("❌ Proibido.", ephemeral=True); return False
        return True
    @discord.ui.button(label="Confirmar Reconstrução", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirmar_button(self, inter, button):
        await inter.response.send_message("⚙️ **Reconstrução iniciada de forma assíncrona!**", ephemeral=True)
        for item in self.children: item.disabled = True
        await inter.message.edit(view=self); asyncio.create_task(reconstruir_servidor_completo(self.guild_alvo, self.template_dados, inter.channel))

class PainelAdminView(discord.ui.View):
    def __init__(self, bot_inst, autor_id: int):
        super().__init__(timeout=180); self.bot = bot_inst; self.autor_id = autor_id
    async def interaction_check(self, inter) -> bool:
        if inter.user.id != self.autor_id: await inter.response.send_message("❌ Acesso negado.", ephemeral=True); return False
        return True
    @discord.ui.button(label="Status do Sistema", style=discord.ButtonStyle.primary, emoji="🖥️")
    async def status_button(self, inter, button):
        lat = f"{self.bot.latency * 1000:.0f}ms" if self.bot.is_ready() else "N/A"
        embed = discord.Embed(title="🖥️ Status Detalhado", color=discord.Color.blue()).add_field(name="Ping", value=f"`{lat}`").add_field(name="Guildas", value=f"`{len(self.bot.guilds)}`")
        await inter.response.send_message(embed=embed, ephemeral=True)
    @discord.ui.button(label="Forçar Sync Gist", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def sync_gist_button(self, inter, button):
        await inter.response.defer(ephemeral=True)
        try:
            from database import sincronizar_banco_local; sincronizar_banco_local()
            await inter.followup.send("✅ **Sync concluído!**", ephemeral=True)
        except Exception as e: await inter.followup.send(f"❌ Erro: {e}", ephemeral=True)
    @discord.ui.button(label="Baixar Backup", style=discord.ButtonStyle.secondary, emoji="📁")
    async def backup_button(self, inter, button):
        if os.path.exists("local_db.json"): await inter.response.send_message(content="📄 Backup cache:", file=discord.File("local_db.json"), ephemeral=True)
        else: await inter.response.send_message("❌ Vazio.", ephemeral=True)
    @discord.ui.button(label="Sincronizar Slash (/) ", style=discord.ButtonStyle.success, emoji="🔨")
    async def sync_slash_button(self, inter, button):
        await inter.response.defer(ephemeral=True)
        try: synced = await self.bot.tree.sync(); await inter.followup.send(f"✅ Sincronizados `{len(synced)}` slashes.", ephemeral=True)
        except Exception as e: await inter.followup.send(f"❌ Erro sync: {e}", ephemeral=True)

class NotaEditModal(discord.ui.Modal):
    def __init__(self, view_parent):
        super().__init__(title="✏️ Editar Bloco de Notas")
        self.view_parent = view_parent
        self.texto_input = discord.ui.TextInput(
            label="Escreva seu texto (Máx 4000 caracteres)",
            style=discord.TextStyle.long,
            default=self.view_parent.active_text[:4000],
            max_length=4000,
            required=True
        )
        self.add_item(self.texto_input)

    async def on_submit(self, inter: discord.Interaction):
        self.view_parent.active_text = self.texto_input.value
        self.view_parent.alterado = True
        embed = discord.Embed(title=f"📝 Bloco de Notas Pessoal (Slot {self.view_parent.slot})", description=self.view_parent.active_text, color=discord.Color.green())
        embed.set_footer(text="Atenção: Clique em Salvar para enviar para a nuvem!")
        await inter.response.edit_message(embed=embed, view=self.view_parent)

class NotaView(discord.ui.View):
    def __init__(self, bot_inst, user_id: int, slot: int, initial_text: str):
        super().__init__(timeout=900); self.bot = bot_inst; self.user_id = user_id; self.slot = slot; self.active_text = initial_text; self.alterado = False
    async def on_timeout(self):
        if self.alterado: db_set(f"notes/{self.user_id}/{self.slot}", self.active_text)
    @discord.ui.button(label="Editar", style=discord.ButtonStyle.primary, emoji="✍️")
    async def editar_button(self, inter, button): await inter.response.send_modal(NotaEditModal(self))
    @discord.ui.button(label="Salvar", style=discord.ButtonStyle.success, emoji="💾")
    async def salvar_button(self, inter, button):
        db_set(f"notes/{self.user_id}/{self.slot}", self.active_text); self.alterado = False
        await inter.response.send_message("💾 **Sua nota foi gravada e salva com sucesso na nuvem!**", ephemeral=True)
    @discord.ui.button(label="Sair (salva auto)", style=discord.ButtonStyle.danger, emoji="🚪")
    async def sair_button(self, inter, button):
        db_set(f"notes/{self.user_id}/{self.slot}", self.active_text)
        for item in self.children: item.disabled = True
        embed = discord.Embed(title="🚪 Sessão Finalizada", description="Sessão de edição encerrada. Suas notas estão seguras na nuvem!", color=discord.Color.red())
        await inter.response.edit_message(embed=embed, view=None); self.stop()

class BotCommands(commands.Cog):
    def __init__(self, bot_inst): self.bot = bot_inst
    @commands.command(name="ping")
    async def ping_prefix(self, ctx): await ctx.send(f"🏓 **Pong!** `{round(self.bot.latency * 1000)}ms`.")
    @app_commands.command(name="ping", description="Verifica a latência atual do bot.")
    async def ping_slash(self, inter): await inter.response.send_message(f"🎲 **Pong!** `{self.bot.latency * 1000:.0f}ms`.", ephemeral=True)
    @commands.command(name="io-write")
    async def io_write_prefix(self, ctx, *, texto: str = None):
        if not texto: await ctx.send("❌ Exemplo: `#io-write Teste`"); return
        if db_set("teste/mensagem", texto): await ctx.send(f"✅ Salvo:\n`{texto}`")
        else: await ctx.send("❌ Falha.")
    @app_commands.command(name="io-write", description="Grava um texto de teste no banco de dados em nuvem.")
    @app_commands.describe(texto="Texto")
    async def io_write_slash(self, inter, texto: str):
        if db_set("teste/mensagem", texto): await inter.response.send_message(f"✅ Salvo:\n`{texto}`", ephemeral=True)
        else: await inter.response.send_message("❌ Falha.", ephemeral=True)
    @commands.command(name="io-read")
    async def io_read_prefix(self, ctx):
        t = db_get("teste/mensagem")
        if t: await ctx.send(f"📖 Lido:\n`{t}`")
        else: await ctx.send("🔍 Vazio.")
    @app_commands.command(name="io-read", description="Lê o texto de teste salvo no banco de dados em nuvem.")
    async def io_read_slash(self, inter):
        t = db_get("teste/mensagem")
        if t: await inter.response.send_message(f"📖 Lido:\n`{t}`", ephemeral=True)
        else: await inter.response.send_message("🔍 Vazio.", ephemeral=True)
    @commands.command(name="senha-adm")
    async def senha_adm_prefix(self, ctx):
        if ctx.author.id != DONO_BOT_ID: await ctx.send("❌ Apenas desenvolvedor master."); return
        s = obter_senha_admin() or "Acesse o painel para definir."
        try:
            dm = await ctx.author.create_dm(); m = await dm.send(f"🔑 **Senha Master:** `{s}` *(Deleta em 5s)*")
            await ctx.send("✅ Senha enviada na DM."); await asyncio.sleep(5); await m.delete()
        except Exception as e: await ctx.send(f"❌ DM fechada. Erro: {e}")
    @app_commands.command(name="senha-adm", description="Envia a senha do painel do Flask na sua DM e a apaga após 5 segundos.")
    async def senha_adm_slash(self, inter):
        if inter.user.id != DONO_BOT_ID: await inter.response.send_message("❌ Dono apenas.", ephemeral=True); return
        s = obter_senha_admin() or "Acesse o painel."
        try:
            dm = await inter.user.create_dm(); m = await dm.send(f"🔑 **Senha Master:** `{s}` *(Deleta em 5s)*")
            await inter.response.send_message("✅ Enviada.", ephemeral=True); await asyncio.sleep(5); await m.delete()
        except Exception as e: await inter.response.send_message(f"❌ Erro: {e}", ephemeral=True)
    @commands.command(name="painel-adm")
    async def painel_adm_prefix(self, ctx):
        if ctx.author.id != DONO_BOT_ID: await ctx.send("❌ Dono apenas."); return
        await ctx.send(embed=discord.Embed(title="⚙️ Painel Master", description="Escolha uma opção:", color=discord.Color.dark_red()), view=PainelAdminView(self.bot, ctx.author.id))
    @app_commands.command(name="painel-adm", description="Abre o painel interativo de administração master (Apenas para o dono do bot).")
    async def painel_adm_slash(self, inter):
        if inter.user.id != DONO_BOT_ID: await inter.response.send_message("❌ Dono apenas.", ephemeral=True); return
        await inter.response.send_message(embed=discord.Embed(title="⚙️ Painel Master", description="Escolha uma opção:", color=discord.Color.dark_red()), view=PainelAdminView(self.bot, inter.user.id), ephemeral=True)
    @commands.command(name="copiar")
    async def copiar_prefix(self, ctx):
        if not ctx.guild: await ctx.send("❌ Apenas em guilda."); return
        if not (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID):
            await ctx.send("❌ Sem permissão."); return
        m = await ctx.send("⚙️ **Escaneando estrutura do servidor...**")
        try:
            d = extrair_estrutura_completa_servidor(ctx.guild); db_set(f"templates/{ctx.author.id}", d)
            embed = discord.Embed(title="✅ Copiado!", description="Estrutura de canais e permissões guardada na nuvem.", color=discord.Color.green())
            embed.add_field(name="Cargos", value=f"`{len(d['roles'])}`").add_field(name="Categorias", value=f"`{len(d['categories'])}`").add_field(name="Canais", value=f"`{len(d['channels'])}`")
            await m.delete(); await ctx.send(embed=embed)
        except Exception as e: await m.edit(content=f"❌ Erro ao clonar: {e}")
    @app_commands.command(name="copiar", description="Varre e copia canais, cargos, categorias e permissões para o seu clipboard em nuvem.")
    async def copiar_slash(self, inter):
        if not inter.guild: await inter.response.send_message("❌ Apenas em guilda.", ephemeral=True); return
        if not inter.user.guild_permissions.administrator and inter.user.id != inter.guild.owner_id and inter.user.id != DONO_BOT_ID:
            await inter.response.send_message("❌ Sem permissão.", ephemeral=True); return
        await inter.response.defer(ephemeral=True)
        try:
            d = extrair_estrutura_completa_servidor(inter.guild); db_set(f"templates/{inter.user.id}", d)
            embed = discord.Embed(title="✅ Copiado!", description="Estrutura guardada de forma segura na nuvem.", color=discord.Color.green())
            embed.add_field(name="Cargos", value=f"`{len(d['roles'])}`").add_field(name="Categorias", value=f"`{len(d['categories'])}`").add_field(name="Canais", value=f"`{len(d['channels'])}`")
            await inter.followup.send(embed=embed, ephemeral=True)
        except Exception as e: await inter.followup.send(f"❌ Erro: {e}", ephemeral=True)
    @commands.command(name="colar")
    async def colar_prefix(self, ctx):
        if not ctx.guild: await ctx.send("❌ Apenas em guilda."); return
        if not (ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID): await ctx.send("❌ Apenas donos."); return
        d = db_get(f"templates/{ctx.author.id}")
        if not d: await ctx.send("❌ Clipboard vazio. Use #copiar antes."); return
        await ctx.send(embed=discord.Embed(title="⚠️ AVISO DE CLONAGEM ⚠️", description="Você criará cargos, canais e categorias salvos.\nExecute apenas em servidores vazios.\nDeseja iniciar?", color=discord.Color.orange()), view=ColarConfirmacaoView(self.bot, ctx.author.id, d, ctx.guild))
    @app_commands.command(name="colar", description="Reconstrói canais, cargos, categorias e permissões salvas no seu clipboard neste servidor.")
    async def colar_slash(self, inter):
        if not inter.guild: await inter.response.send_message("❌ Apenas em guilda.", ephemeral=True); return
        if not (inter.user.id == inter.guild.owner_id or inter.user.id == DONO_BOT_ID): await inter.response.send_message("❌ Apenas donos.", ephemeral=True); return
        d = db_get(f"templates/{inter.user.id}")
        if not d: await inter.response.send_message("❌ Clipboard vazio.", ephemeral=True); return
        await inter.response.send_message(embed=discord.Embed(title="⚠️ AVISO DE CLONAGEM ⚠️", description="Você criará cargos, canais e categorias salvos.\nDeseja iniciar?", color=discord.Color.orange()), view=ColarConfirmacaoView(self.bot, inter.user.id, d, inter.guild), ephemeral=True)
    @commands.command(name="monitorar")
    async def monitorar_prefix(self, ctx, user_id: str = None):
        if not (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID):
            await ctx.send("❌ Apenas administradores."); return
        if not user_id: await ctx.send("❌ Forneça o ID."); return
        guild_id = str(ctx.guild.id); m = db_get(f"server_config/{guild_id}/monitorados", [])
        if user_id in m: await ctx.send("⚠️ Já monitorado."); return
        m.append(user_id); db_set(f"server_config/{guild_id}/monitorados", m)
        await ctx.send(f"🛡️ **ID `{user_id}` monitorado com sucesso!**")
    @app_commands.command(name="monitorar", description="Adiciona um ID do Discord à lista de monitoramento de segurança.")
    @app_commands.describe(user_id="O ID do Discord do usuário que deseja monitorar.")
    async def monitorar_slash(self, inter, user_id: str):
        if not inter.guild: await inter.response.send_message("❌ Apenas em guilda.", ephemeral=True); return
        guild_id = str(inter.guild.id)
        if not (inter.user.guild_permissions.administrator or inter.user.id == inter.guild.owner_id or inter.user.id == DONO_BOT_ID):
            await inter.response.send_message("❌ Apenas administradores.", ephemeral=True); return
        m = db_get(f"server_config/{guild_id}/monitorados", [])
        if user_id in m: await inter.response.send_message("⚠️ Já monitorado.", ephemeral=True); return
        m.append(user_id); db_set(f"server_config/{guild_id}/monitorados", m)
        await inter.response.send_message(f"🛡️ **ID `{user_id}` monitorado com sucesso!**", ephemeral=True)
    @commands.command(name="desmonitorar")
    async def desmonitorar_prefix(self, ctx, user_id: str = None):
        if not (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID):
            await ctx.send("❌ Apenas administradores."); return
        if not user_id: await ctx.send("❌ Forneça o ID."); return
        guild_id = str(ctx.guild.id); m = db_get(f"server_config/{guild_id}/monitorados", [])
        if user_id not in m: await ctx.send("❌ ID não monitorado."); return
        m.remove(user_id); db_set(f"server_config/{guild_id}/monitorados", m)
        await ctx.send(f"✅ ID `{user_id}` removido do monitoramento.")
    @app_commands.command(name="desmonitorar", description="Remove um ID do Discord da lista de monitoramento de segurança.")
    @app_commands.describe(user_id="O ID do Discord do usuário que deseja remover.")
    async def desmonitorar_slash(self, inter, user_id: str):
        if not inter.guild: await inter.response.send_message("❌ Apenas em guilda.", ephemeral=True); return
        guild_id = str(inter.guild.id)
        if not (inter.user.guild_permissions.administrator or inter.user.id == inter.guild.owner_id or inter.user.id == DONO_BOT_ID):
            await inter.response.send_message("❌ Apenas administradores.", ephemeral=True); return
        m = db_get(f"server_config/{guild_id}/monitorados", [])
        if user_id not in m: await inter.response.send_message("❌ Não monitorado.", ephemeral=True); return
        m.remove(user_id); db_set(f"server_config/{guild_id}/monitorados", m)
        await inter.response.send_message(f"✅ ID `{user_id}` removido.", ephemeral=True)
    @commands.command(name="monitorados")
    async def monitorados_prefix(self, ctx):
        if not (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id or ctx.author.id == DONO_BOT_ID):
            await ctx.send("❌ Apenas administradores."); return
        guild_id = str(ctx.guild.id); m = db_get(f"server_config/{guild_id}/monitorados", [])
        if not m: await ctx.send("🛡️ Nenhum usuário monitorado."); return
        await ctx.send(embed=discord.Embed(title="🛡️ IDs Sob Vigilância", description="\n".join([f"• `{u}` (<@{uid}>)" for uid in m]), color=discord.Color.red()))
    @app_commands.command(name="monitorados", description="Lista todos os IDs sob monitoramento de segurança neste servidor.")
    async def monitorados_slash(self, inter):
        if not inter.guild: await inter.response.send_message("❌ Apenas em guilda.", ephemeral=True); return
        guild_id = str(inter.guild.id)
        if not (inter.user.guild_permissions.administrator or inter.user.id == interaction.guild.owner_id or inter.user.id == DONO_BOT_ID):
            await inter.response.send_message("❌ Apenas administradores.", ephemeral=True); return
        m = db_get(f"server_config/{guild_id}/monitorados", [])
        if not m: await inter.response.send_message("🛡️ Nenhum usuário monitorado.", ephemeral=True); return
        await inter.response.send_message(embed=discord.Embed(title="🛡️ IDs Sob Vigilância", description="\n".join([f"• `{uid}` (<@{uid}>)" for uid in m]), color=discord.Color.red()), ephemeral=True)

    @app_commands.command(name="nota", description="Gerencia o seu bloco de notas pessoal e permanente na nuvem.")
    @app_commands.describe(
        slot="Número do slot de notas (Membros: 1-5, Dono do Bot: Infinitos!).",
        texto="Texto opcional para salvar de forma direta no slot correspondente (Max 5000 car.)."
    )
    async def nota_slash(self, inter: discord.Interaction, slot: int, texto: str = None):
        user_id = inter.user.id
        is_owner = (user_id == DONO_BOT_ID)
        if not is_owner:
            if slot < 1 or slot > 5:
                await inter.response.send_message("❌ **Acesso negado!** Como membro padrão, você possui acesso aos slots de **1 a 5**.", ephemeral=True)
                return
        else:
            if slot < 1:
                await inter.response.send_message("❌ **Número inválido!** Escolha um slot a partir do número 1.", ephemeral=True)
                return

        guild_id = str(inter.guild.id) if inter.guild else "DirectMessage"
        if texto:
            if len(texto) > 5000:
                await inter.response.send_message("❌ **Limite de caracteres excedido!** O texto enviado possui mais do que os 5000 caracteres máximos permitidos.", ephemeral=True)
                return
            db_set(f"notes/{user_id}/{slot}", texto)
            embed = discord.Embed(title="💾 Notas Salvas na Nuvem!", description=f"Suas anotações foram salvas diretamente no **Slot {slot}**!", color=discord.Color.green())
            await inter.response.send_message(embed=embed, ephemeral=True)
            return

        texto_salvo = db_get(f"notes/{user_id}/{slot}", "Seu bloco de notas está vazio! Clique em Editar para escrever alguma coisa.")
        embed = discord.Embed(title=f"📝 Bloco de Notas Pessoal (Slot {slot})", description=texto_salvo, color=discord.Color.blue())
        view = NotaView(self.bot, user_id, slot, texto_salvo)
        await inter.response.send_message(embed=embed, view=view, ephemeral=True)