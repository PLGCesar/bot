import discord
from discord.ext import commands
import threading
import asyncio
import os

from database import TOKEN, logger, sincronizar_banco_local
from utils import registrar_execucao_comando, db_get
from commands_bot import BotCommands
from web import rodar_servidor_web

intents = discord.Intents.default()
intents.message_content = True 
intents.members = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="#", intents=intents, help_command=None)
        
    async def setup_hook(self):
        await self.add_cog(BotCommands(self))

bot = MyBot()


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


# --- EVENTO ON_READY ---

@bot.event
async def on_ready():
    logger.info(f"Bot do Discord conectado com sucesso como {bot.user}")
    
    # Injeta a referência ativa do bot no web.py de forma dinâmica
    import web
    web.bot_ref = bot
    
    # Sincroniza o banco de dados do GitHub Gist com o cache local
    sincronizar_banco_local()
    
    try:
        synced = await bot.tree.sync()
        logger.info(f"Sincronizados {len(synced)} comandos de barra.")
    except Exception as e:
        logger.error(f"Erro ao sincronizar comandos de barra: {e}")


# --- INICIALIZAÇÃO MULTI-THREADING ---

if __name__ == "__main__":
    # Dispara o Flask importado do web.py em segundo plano (O Render fará o bind instantâneo)
    thread_web = threading.Thread(target=rodar_servidor_web, daemon=True)
    thread_web.start()
    
    # Validação de segurança do Token do Discord
    if not TOKEN:
        logger.critical("DISCORD_TOKEN ausente nas variáveis de ambiente.")
    else:
        try:
            bot.run(TOKEN)
        except discord.errors.LoginFailure:
            logger.critical("Token inválido.")
        except Exception as e:
            logger.critical(f"Erro crítico de boot: {e}")