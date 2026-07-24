import discord
from discord.ext import commands
from discord import app_commands
import asyncio, uuid

from database import DONO_BOT_ID, db_get, db_set, logger
from utils import deserializar_permissoes_canal, extrair_estrutura_completa_servidor, analisar_texto, corrigir_texto_sem_ia, chamar_gemini

async def reconstruir_servidor_completo(guild: discord.Guild, data: dict, canal_logs: discord.TextChannel):
    roles_data = data.get("roles", []); categories_data = data.get("categories", []); channels_data = data.get("channels", []); role_map = {}
    for r_info in roles_data:
        try:
            role_map[str(r_info["id"])] = await guild.create_role(name=r_info["name"], color=discord.Color(r_info["color"]), hoist=r_info["hoist"], mentionable=r_info["mentionable"], permissions=discord.Permissions(r_info["permissions"]), reason="[CLONER] cargos")
            await asyncio.sleep(0.5)
        except Exception as e: logger.error(f"Erro cargo: {e}")
    category_map = {}
    for cat_info in categories_data:
        try:
            category_map[str(cat_info["id"])] = await guild.create_category(name=cat_info["name"], overwrites=deserializar_permissoes_canal(cat_info["overwrites"], guild, role_map), position=cat_info["position"], reason="[CLONER] categorias")
            await asyncio.sleep(0.5)
        except Exception as e: logger.error(f"Erro cat: {e}")
    for ch_info in channels_data:
        try:
            ow = deserializar_permissoes_canal(ch_info["overwrites"], guild, role_map); cat = category_map.get(str(ch_info["category_id"])) if ch_info["category_id"] else None
            if ch_info["type"] == "text": await guild.create_text_channel(name=ch_info["name"], category=cat, topic=ch_info["topic"], overwrites=ow, position=ch_info["position"], reason="[CLONER]")
            elif ch_info["type"] == "voice": await guild.create_voice_channel(name=ch_info["name"], category=cat, overwrites=ow, position=ch_info["position"], reason="[CLONER]")
            await asyncio.sleep(0.5)
        except Exception as e: logger.error(f"Erro canal: {e}")
    try: await canal_logs.send(embed=discord.Embed(title="✨ Reconstrução Concluída com Sucesso! ✨", color=discord.Color.green()))
    except Exception: pass

class ColarConfirmacaoView(discord.ui.View):
    def __init__(self, bot_inst, autor_id: int, template_dados: dict, guild_alvo):
        super().__init__(timeout=60); self.bot = bot_inst; self.autor_id = autor_id; self.template_dados = template_dados; self.guild_alvo = guild_alvo
    async def interaction_check(self, inter) -> bool:
        if inter.user.id != self.autor_id: await inter.response.send_message("❌ Apenas o autor pode confirmar!", ephemeral=True); return False
        return True
    @discord.ui.button(label="Confirmar Reconstrução", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirmar_button(self, inter, button):
        await inter.response.send_message("⚙️ **Reconstruindo em segundo plano!** Aguarde a mágica acontecer.", ephemeral=True)
        for item in self.children: item.disabled = True
        await inter.message.edit(view=self); asyncio.create_task(reconstruir_servidor_completo(self.guild_alvo, self.template_dados, inter.channel))

class AvaliarView(discord.ui.View):
    def __init__(self, autor_id: int, texto: str):
        super().__init__(timeout=300)
        self.autor_id = autor_id
        self.texto = texto

    async def interaction_check(self, inter) -> bool:
        if inter.user.id != self.autor_id: await inter.response.send_message("❌ Ei! Só quem enviou o texto pode usar os botões!", ephemeral=True); return False
        return True

    @discord.ui.button(label="Salvar", style=discord.ButtonStyle.success, emoji="💾")
    async def btn_salvar(self, inter, button):
        uid = str(inter.user.id)
        is_dono = (inter.user.id == DONO_BOT_ID)
        limite = 750 if is_dono else 5
        banco = db_get(f"avaliacoes/{uid}", {})
        if len(banco) >= limite:
            await inter.response.send_message(f"❌ Poxa, você atingiu seu limite de {limite} slots salvos!", ephemeral=True)
            return
        id_salvo = str(uuid.uuid4())[:8]
        banco[id_salvo] = self.texto
        db_set(f"avaliacoes/{uid}", banco)
        await inter.response.send_message(f"✅ **Salvo com sucesso!** A ID do seu texto é `{id_salvo}`.", ephemeral=True)

    @discord.ui.button(label="Corrigir Básico", style=discord.ButtonStyle.primary, emoji="🛠️")
    async def btn_basico(self, inter, button):
        await inter.response.defer(ephemeral=True)
        corrigido = corrigir_texto_sem_ia(self.texto)
        em = discord.Embed(title="🛠️ Correção Básica Concluída!", description=corrigido, color=discord.Color.orange())
        await inter.followup.send(embed=em, ephemeral=True)

    @discord.ui.button(label="IA: Modo Normal", style=discord.ButtonStyle.secondary, emoji="🤖")
    async def btn_ia_normal(self, inter, button):
        await inter.response.defer(ephemeral=True)
        sys1 = "Aja como um editor humano profissional. Corrija a gramática e fluidez, mas mantenha rigorosamente o tom, as gírias intencionais e a personalidade original do usuário. O texto não deve parecer robótico ou gerado por IA. Retorne APENAS o texto corrigido."
        sys2 = "Você é o revisor final. Leia este texto ajustado e remova qualquer frase que soe clichê de IA. Devolva um fluxo 100% natural, conversacional e orgânico. Retorne APENAS o texto final."
        r1, idx = await chamar_gemini(sys1, self.texto)
        if r1.startswith("❌"): await inter.followup.send(r1, ephemeral=True); return
        r2, _ = await chamar_gemini(sys2, r1, start_idx=idx)
        em = discord.Embed(title="🤖 Texto Reconstruído por IA (Modo Normal)", description=r2, color=discord.Color.purple())
        await inter.followup.send(embed=em, ephemeral=True)

    @discord.ui.button(label="IA: Só Dicas", style=discord.ButtonStyle.secondary, emoji="💡")
    async def btn_ia_dicas(self, inter, button):
        await inter.response.defer(ephemeral=True)
        sys1 = "Aja como um professor de escrita sênior. Não reescreva o texto inteiro. Apenas forneça dicas práticas e estruturadas em tópicos para melhorar a gramática, vocabulário e coesão."
        sys2 = "Você é o revisor amigável. Pegue as dicas brutas fornecidas e formate-as de maneira alegre, encorajadora e fácil de ler usando emojis divertidos."
        r1, idx = await chamar_gemini(sys1, self.texto)
        if r1.startswith("❌"): await inter.followup.send(r1, ephemeral=True); return
        r2, _ = await chamar_gemini(sys2, r1, start_idx=idx)
        em = discord.Embed(title="💡 Dicas Profissionais de Escrita", description=r2, color=discord.Color.gold())
        await inter.followup.send(embed=em, ephemeral=True)


class BotCommands(commands.Cog):
    def __init__(self, bot_inst): self.bot = bot_inst

    @app_commands.command(name="copiar", description="Varre e copia canais, cargos, categorias e permissões para o seu clipboard em nuvem.")
    async def copiar_slash(self, inter: discord.Interaction):
        if not inter.guild: await inter.response.send_message("❌ Apenas em servidores.", ephemeral=True); return
        if not inter.user.guild_permissions.administrator and inter.user.id != inter.guild.owner_id and inter.user.id != DONO_BOT_ID:
            await inter.response.send_message("❌ Permissão negada.", ephemeral=True); return
        await inter.response.defer(ephemeral=True)
        try:
            d = extrair_estrutura_completa_servidor(inter.guild); db_set(f"templates/{inter.user.id}", d)
            embed = discord.Embed(title="✅ Magia feita! Servidor Copiado!", description="Toda a estrutura foi guardada com segurança na nuvem.", color=discord.Color.green())
            embed.add_field(name="Cargos", value=f"`{len(d['roles'])}`").add_field(name="Categorias", value=f"`{len(d['categories'])}`").add_field(name="Canais", value=f"`{len(d['channels'])}`")
            await inter.followup.send(embed=embed, ephemeral=True)
        except Exception as e: await inter.followup.send(f"❌ Vish, deu erro: {e}", ephemeral=True)

    @app_commands.command(name="colar", description="Reconstrói canais, cargos, categorias salvas no seu clipboard neste servidor.")
    async def colar_slash(self, inter: discord.Interaction):
        if not inter.guild: await inter.response.send_message("❌ Apenas em servidores.", ephemeral=True); return
        if not (inter.user.id == inter.guild.owner_id or inter.user.id == DONO_BOT_ID): await inter.response.send_message("❌ Apenas o dono absoluto do servidor pode fazer isso.", ephemeral=True); return
        d = db_get(f"templates/{inter.user.id}")
        if not d: await inter.response.send_message("❌ Seu clipboard está vazio! Copie algo primeiro.", ephemeral=True); return
        em = discord.Embed(title="⚠️ PERIGO: CLONAGEM DE SERVIDOR ⚠️", description="Você está prestes a despejar a estrutura de outro servidor aqui.\nTem certeza absoluta?", color=discord.Color.orange())
        await inter.response.send_message(embed=em, view=ColarConfirmacaoView(self.bot, inter.user.id, d, inter.guild), ephemeral=True)

    @app_commands.command(name="avaliar", description="Avalia um texto exibindo caracteres, correção ortográfica simples ou via Inteligência Artificial.")
    @app_commands.describe(texto="O texto completo que você deseja que o bot analise e corrija.")
    async def avaliar_slash(self, inter: discord.Interaction, texto: str):
        stats = analisar_texto(texto)
        em = discord.Embed(title="📊 Análise Completa de Texto", description="Seu texto foi escaneado com sucesso! Escolha uma ação abaixo:", color=discord.Color.blue())
        em.add_field(name="Caracteres Totais", value=f"`{stats['totais']}`", inline=True)
        em.add_field(name="Sem Espaços", value=f"`{stats['sem_espaco']}`", inline=True)
        em.add_field(name="Espaços", value=f"`{stats['espacos']}`", inline=True)
        em.add_field(name="Palavras", value=f"`{stats['palavras']}`", inline=True)
        em.add_field(name="Linhas", value=f"`{stats['linhas']}`", inline=True)
        await inter.response.send_message(embed=em, view=AvaliarView(inter.user.id, texto), ephemeral=True)