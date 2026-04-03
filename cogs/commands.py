# cogs/commands.py
# Gộp tất cả lệnh Discord: combat, macro, hub, npc, admin
# Sử dụng app_commands.Group để có cú pháp /combat set, /macro initiative, ...

import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, List, Dict, Any
import asyncio
import re

from core.gsheet_client import GoogleSheetsClient
from core.initializer import load_global_config, load_effect_pool
from core.parser import parse
from core.evaluator import Evaluator
from core.function_registry import FunctionRegistry
from core.macro_executor import MacroExecutor
from core.combat_engine import CombatEngine
from models.actor import ActorRuntime
from models.combat_models import CombatActor, Combat, PlayerState, CombatPhase
from config import COMBAT_ACTION_MARKER, COMBAT_TARGETED_MARKER, COMBAT_KO_MARKER, SHEET_INDEX
from utils.helpers import get_logger

logger = get_logger()


class TTRPGCog(commands.Cog):
    def __init__(self, bot: commands.Bot, gsheet_client: GoogleSheetsClient):
        self.bot = bot
        self.gsheet = gsheet_client

        self.function_registry = FunctionRegistry()
        self.evaluator = Evaluator(self.function_registry)
        self.macro_executor = MacroExecutor(self.evaluator)
        self.combat_engine = CombatEngine(self.macro_executor, self.evaluator)

        self.actor_cache: Dict[int, ActorRuntime] = {}
        self.global_config: Dict[str, str] = {}
        self.global_effect_pool: Dict[str, str] = {}
        self.npc_roles: Dict[int, CombatActor] = {}          # role_id -> CombatActor
        self.user_control: Dict[int, int] = {}              # user_id -> role_id đang điều khiển

        # Các nhóm lệnh
        self.combat_group = app_commands.Group(name="combat", description="Quản lý combat")
        self.macro_group = app_commands.Group(name="macro", description="Gọi macro")
        self.npc_group = app_commands.Group(name="npc", description="Quản lý NPC")
        self.admin_group = app_commands.Group(name="admin", description="Quản trị")

    async def cog_load(self):
        self.global_config = await load_global_config(self.gsheet)
        self.global_effect_pool = await load_effect_pool(self.gsheet)
        self.evaluator.global_config = self.global_config
        self.evaluator.global_effect_pool = self.global_effect_pool
        await self._load_all_bindings()
        logger.info("Đã load global config, effect pool và bindings")

    async def _load_all_bindings(self):
        try:
            sheet = await self.gsheet.spreadsheet.worksheet(SHEET_INDEX)
            rows = await sheet.get_all_values()
            if len(rows) < 2:
                return
            headers = rows[0]
            discord_col = headers.index("discord_id") if "discord_id" in headers else 1
            sheet_col = headers.index("sheet_id") if "sheet_id" in headers else 2
            name_col = headers.index("actor_name") if "actor_name" in headers else 3
            for row in rows[1:]:
                if len(row) <= max(discord_col, sheet_col, name_col):
                    continue
                try:
                    uid = int(row[discord_col])
                    sheet_id = row[sheet_col]
                    actor_name = row[name_col]
                    if uid not in self.actor_cache:
                        actor = ActorRuntime(actor_id=sheet_id, discord_user_id=uid, name=actor_name)
                        await self._load_actor_from_sheet(actor)
                        self.actor_cache[uid] = actor
                except:
                    continue
        except Exception as e:
            logger.error(f"Lỗi load bindings: {e}")

    async def _load_actor_from_sheet(self, actor: ActorRuntime):
        data = await self.gsheet.batch_load_by_load_column(actor.actor_id)
        for var_name, col_map in data.items():
            for idx, value in col_map.items():
                key = f"{var_name}.{idx}" if idx else var_name
                actor.runtime_vars[key] = value
                content = str(value)
                if content.startswith('{') and '=' in content:
                    try:
                        ast = parse(content)
                        from core.ast_nodes import DirectiveOverwriteNode, DirectiveMacroNode, DirectiveEffectNode
                        if isinstance(ast, DirectiveOverwriteNode):
                            self.evaluator.reset_context()
                            self.evaluator.current_actor = actor
                            await self.evaluator.evaluate(ast)
                        elif isinstance(ast, DirectiveMacroNode):
                            actor.add_macro(ast.macro_name, await self.evaluator._serialize_expression(ast.expression), ast.tag)
                    except:
                        pass
                elif content.startswith('{') and '//' in content:
                    try:
                        ast = parse(content)
                        from core.ast_nodes import DirectiveMacroNode
                        if isinstance(ast, DirectiveMacroNode):
                            actor.add_macro(ast.macro_name, await self.evaluator._serialize_expression(ast.expression), ast.tag)
                    except:
                        pass

    # -------------------- HUB --------------------
    @app_commands.command(name="hub", description="Mở actor hub của bạn")
    async def hub(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        if user_id in self.actor_cache:
            actor = self.actor_cache[user_id]
            await self._send_hub_message(interaction, actor)
        else:
            modal = CreateActorModal(self.gsheet, self.actor_cache, self)
            await interaction.response.send_modal(modal)

    async def _send_hub_message(self, interaction: discord.Interaction, actor: ActorRuntime):
        embed = discord.Embed(title=f"Actor Hub: {actor.name}", color=0x3498db)
        view = HubView(actor, self.evaluator, self.global_config)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # -------------------- COMBAT GROUP --------------------
    @combat_group.command(name="set", description="Mở combat mới tại kênh này")
    async def combat_set(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        if self.combat_engine.get_combat(channel_id):
            await interaction.response.send_message("Đã có combat trong kênh này!", ephemeral=True)
            return
        combat = await self.combat_engine.create_combat(channel_id)
        embed = discord.Embed(title="⚔️ COMBAT ⚔️", description="Chưa có người tham gia", color=0xe67e22)
        msg = await interaction.channel.send(embed=embed)
        combat.sticky_message_id = msg.id
        await interaction.response.send_message("Đã tạo combat! Dùng `/combat join` để tham gia.", ephemeral=True)

    @combat_group.command(name="join", description="Tham gia combat")
    async def combat_join(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        channel_id = interaction.channel_id
        combat = self.combat_engine.get_combat(channel_id)
        if not combat:
            await interaction.response.send_message("Chưa có combat trong kênh này! Dùng `/combat set` để tạo.", ephemeral=True)
            return
        if user_id not in self.actor_cache:
            await interaction.response.send_message("Bạn chưa có actor! Dùng `/hub` để tạo.", ephemeral=True)
            return
        actor_runtime = self.actor_cache[user_id]
        combat_actor = CombatActor(
            actor_id=actor_runtime.actor_id,
            name=actor_runtime.name,
            discord_user_id=user_id,
            base_vars=actor_runtime.runtime_vars.copy(),
            macro_pool=actor_runtime.macro_pool.copy(),
            passive_pool=actor_runtime.passive_pool.copy()
        )
        await self.combat_engine.add_actor(combat, combat_actor)
        await interaction.response.send_message(f"{actor_runtime.name} đã tham gia combat!", ephemeral=True)
        await self._update_combat_ui(combat)

    @combat_group.command(name="team", description="Chọn team")
    async def combat_team(self, interaction: discord.Interaction, team_name: Optional[str] = None):
        user_id = interaction.user.id
        channel_id = interaction.channel_id
        combat = self.combat_engine.get_combat(channel_id)
        if not combat:
            await interaction.response.send_message("Chưa có combat!", ephemeral=True)
            return
        actor = combat.get_actor_by_user_id(user_id)
        if not actor:
            await interaction.response.send_message("Bạn chưa tham gia combat!", ephemeral=True)
            return
        if team_name:
            combat.add_actor_to_team(actor, team_name)
            await interaction.response.send_message(f"{actor.name} đã gia nhập team {team_name}", ephemeral=True)
        else:
            old_team = combat.get_team_of(actor)
            if old_team:
                combat.teams[old_team].remove(actor)
            await interaction.response.send_message(f"{actor.name} đã rời team, trở thành trung lập", ephemeral=True)
        await self._update_combat_ui(combat)

    @combat_group.command(name="start", description="Bắt đầu combat")
    async def combat_start(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        combat = self.combat_engine.get_combat(channel_id)
        if not combat:
            await interaction.response.send_message("Chưa có combat!", ephemeral=True)
            return
        if len(combat.actors) < 2:
            await interaction.response.send_message("Cần ít nhất 2 người tham gia!", ephemeral=True)
            return
        await self.combat_engine.start_combat(combat)
        await self._update_combat_ui(combat)
        await interaction.response.send_message("Combat bắt đầu! Mọi người dùng `/macro initiative` để roll initiative.", ephemeral=True)

    @combat_group.command(name="skip", description="Bỏ qua phase hiện tại")
    async def combat_skip(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        combat = self.combat_engine.get_combat(channel_id)
        if not combat:
            await interaction.response.send_message("Chưa có combat!", ephemeral=True)
            return
        await self.combat_engine.skip_phase(combat)
        await self._update_combat_ui(combat)
        await interaction.response.send_message("Đã bỏ qua phase", ephemeral=True)

    @combat_group.command(name="flee", description="Rời khỏi combat (hoặc kick người khác nếu có quyền)")
    async def combat_flee(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        channel_id = interaction.channel_id
        combat = self.combat_engine.get_combat(channel_id)
        if not combat:
            await interaction.response.send_message("Chưa có combat!", ephemeral=True)
            return
        if member and member.id != interaction.user.id:
            if not interaction.user.guild_permissions.manage_messages:
                await interaction.response.send_message("Bạn không có quyền kick người khác!", ephemeral=True)
                return
            actor = combat.get_actor_by_user_id(member.id)
            if not actor:
                await interaction.response.send_message("Người này không trong combat!", ephemeral=True)
                return
            await self.combat_engine.remove_actor(combat, actor, is_flee=True)
            await interaction.response.send_message(f"Đã kick {actor.name} khỏi combat", ephemeral=True)
        else:
            actor = combat.get_actor_by_user_id(interaction.user.id)
            if not actor:
                await interaction.response.send_message("Bạn không trong combat!", ephemeral=True)
                return
            await self.combat_engine.remove_actor(combat, actor, is_flee=True)
            await interaction.response.send_message(f"{actor.name} đã rời khỏi combat", ephemeral=True)
        await self._update_combat_ui(combat)

    @combat_group.command(name="end", description="Kết thúc combat ngay lập tức")
    async def combat_end(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        combat = self.combat_engine.get_combat(channel_id)
        if not combat:
            await interaction.response.send_message("Chưa có combat!", ephemeral=True)
            return
        await self.combat_engine.end_combat(combat)
        await interaction.response.send_message("Combat đã kết thúc!", ephemeral=True)

    # -------------------- MACRO GROUP --------------------
    @macro_group.command(name="call", description="Gọi macro")
    async def macro_call(self, interaction: discord.Interaction, macro_name: str, targets: str = ""):
        user_id = interaction.user.id
        channel_id = interaction.channel_id
        combat = self.combat_engine.get_combat(channel_id)

        # Xác định actor hiện tại (có thể là NPC nếu đang điều khiển)
        actor = None
        if user_id in self.user_control:
            role_id = self.user_control[user_id]
            actor = self.npc_roles.get(role_id)
        if not actor:
            if user_id not in self.actor_cache:
                await interaction.response.send_message("Bạn chưa có actor! Dùng `/hub` để tạo.", ephemeral=True)
                return
            if combat:
                actor = combat.get_actor_by_user_id(user_id)
                if not actor:
                    await interaction.response.send_message("Bạn chưa tham gia combat!", ephemeral=True)
                    return
            else:
                actor = self.actor_cache[user_id]

        target_list = []
        if targets:
            mention_ids = re.findall(r'<@!?(\d+)>', targets)
            for uid_str in mention_ids:
                uid = int(uid_str)
                if combat:
                    t = combat.get_actor_by_user_id(uid)
                    if t:
                        target_list.append(t)
                else:
                    if uid in self.actor_cache:
                        target_list.append(self.actor_cache[uid])
        if not target_list and combat:
            target_list = [actor]

        if combat:
            msg, success = await self.combat_engine.execute_macro_in_combat(combat, actor, macro_name, target_list)
        else:
            msg, success = await self.macro_executor.execute(
                actor, macro_name, target_list, in_combat=False,
                global_config=self.global_config, global_effect_pool=self.global_effect_pool
            )
        await interaction.response.send_message(msg, ephemeral=True)
        if combat:
            await self._update_combat_ui(combat)

    @macro_group.command(name="initiative", description="Roll initiative (macro mặc định)")
    async def macro_initiative(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        channel_id = interaction.channel_id
        combat = self.combat_engine.get_combat(channel_id)
        if not combat:
            await interaction.response.send_message("Không có combat!", ephemeral=True)
            return
        actor = combat.get_actor_by_user_id(user_id)
        if not actor:
            await interaction.response.send_message("Bạn chưa tham gia combat!", ephemeral=True)
            return
        if combat.phase != CombatPhase.TURN_READY:
            await interaction.response.send_message("Chỉ có thể roll initiative ở phase TURN READY", ephemeral=True)
            return
        initiative = await self.macro_executor.execute_initiative(actor)
        actor.initiative = initiative
        await interaction.response.send_message(f"{actor.name} roll initiative: {initiative}", ephemeral=True)
        await self._update_combat_ui(combat)

    @macro_group.command(name="skip", description="Bỏ lượt (macro mặc định)")
    async def macro_skip(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        channel_id = interaction.channel_id
        combat = self.combat_engine.get_combat(channel_id)
        if not combat:
            await interaction.response.send_message("Không có combat!", ephemeral=True)
            return
        actor = combat.get_actor_by_user_id(user_id)
        if not actor:
            await interaction.response.send_message("Bạn chưa tham gia combat!", ephemeral=True)
            return
        if combat.phase not in (CombatPhase.ACTION, CombatPhase.REACTION):
            await interaction.response.send_message("Chỉ có thể bỏ lượt ở phase ACTION hoặc REACTION", ephemeral=True)
            return
        if actor.action_limit_remaining <= 0:
            await interaction.response.send_message("Bạn đã hết action limit!", ephemeral=True)
            return
        await self.macro_executor.execute_skip(actor)
        actor.action_limit_remaining = 0
        await interaction.response.send_message(f"{actor.name} đã bỏ lượt!", ephemeral=True)

        if combat.phase == CombatPhase.ACTION:
            actors_in_phase = [a for a in combat.actors if a.player_state == PlayerState.ACTION]
        else:
            actors_in_phase = [a for a in combat.actors if a.player_state == PlayerState.BE_TARGETED]
        all_done = all(a.action_limit_remaining <= 0 for a in actors_in_phase)
        if all_done:
            if combat.phase == CombatPhase.ACTION:
                combat.phase = CombatPhase.REACTION
            elif combat.phase == CombatPhase.REACTION:
                combat.phase = CombatPhase.END_PHASE
            await self.combat_engine.next_phase(combat)
        await self._update_combat_ui(combat)

    # -------------------- NPC GROUP --------------------
    @npc_group.command(name="spawn", description="Triệu hồi NPC từ sheet")
    async def npc_spawn(self, interaction: discord.Interaction, source: str, quantity: int = 1, name: Optional[str] = None):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Lệnh này chỉ dùng trong server!", ephemeral=True)
            return
        role_name = f"NPC_{source}_{interaction.user.id}_{quantity}"
        role = await guild.create_role(name=role_name, mentionable=True)
        data = await self.gsheet.batch_load_by_load_column(source)
        for i in range(quantity):
            npc_name = name if name else f"{source}_{i+1}"
            actor = CombatActor(
                actor_id=source,
                name=npc_name,
                discord_user_id=interaction.user.id,
                base_vars={},
                macro_pool={},
                passive_pool={}
            )
            for var_name, col_map in data.items():
                for idx, value in col_map.items():
                    actor.base_vars[f"{var_name}.{idx}"] = value
            self.npc_roles[role.id] = actor
            combat = self.combat_engine.get_combat(interaction.channel_id)
            if combat:
                await self.combat_engine.add_actor(combat, actor)
                await self._update_combat_ui(combat)
        await interaction.response.send_message(f"Đã triệu hồi {quantity} NPC với role {role.mention}", ephemeral=True)

    @npc_group.command(name="set", description="Điều khiển NPC bằng role")
    async def npc_set(self, interaction: discord.Interaction, role: discord.Role):
        if role.id not in self.npc_roles:
            await interaction.response.send_message("Role này không phải NPC!", ephemeral=True)
            return
        self.user_control[interaction.user.id] = role.id
        await interaction.response.send_message(f"Bạn đang điều khiển NPC {role.name}", ephemeral=True)

    @npc_group.command(name="despawn", description="Xóa NPC")
    async def npc_despawn(self, interaction: discord.Interaction, role: discord.Role):
        if role.id not in self.npc_roles:
            await interaction.response.send_message("Role này không phải NPC!", ephemeral=True)
            return
        actor = self.npc_roles.pop(role.id)
        combat = self.combat_engine.get_combat(interaction.channel_id)
        if combat:
            await self.combat_engine.remove_actor(combat, actor, is_flee=True)
            await self._update_combat_ui(combat)
        await role.delete()
        await interaction.response.send_message(f"Đã xóa NPC {actor.name}", ephemeral=True)

    # -------------------- ADMIN GROUP --------------------
    @admin_group.command(name="reload", description="Reload toàn bộ cache và dữ liệu từ GGS")
    @app_commands.default_permissions(administrator=True)
    async def reload(self, interaction: discord.Interaction):
        self.gsheet.clear_cache()
        self.global_config = await load_global_config(self.gsheet)
        self.global_effect_pool = await load_effect_pool(self.gsheet)
        self.evaluator.global_config = self.global_config
        self.evaluator.global_effect_pool = self.global_effect_pool
        self.actor_cache.clear()
        await self._load_all_bindings()
        await interaction.response.send_message("Đã reload toàn bộ dữ liệu!", ephemeral=True)

    # -------------------- HÀM HỖ TRỢ UI --------------------
    async def _update_combat_ui(self, combat: Combat):
        if not combat.sticky_message_id:
            return
        channel = self.bot.get_channel(combat.channel_id)
        if not channel:
            return
        try:
            msg = await channel.fetch_message(combat.sticky_message_id)
        except:
            return
        embed = discord.Embed(title=f"⚔️ COMBAT - Turn {combat.turn_count} ⚔️", color=0xe67e22)
        embed.add_field(name="Phase", value=combat.phase.value, inline=False)
        lines = []
        for actor in combat.initiative_queue if combat.initiative_queue else combat.actors:
            marker = ""
            if actor.player_state == PlayerState.ACTION:
                marker = COMBAT_ACTION_MARKER
            elif actor.player_state == PlayerState.BE_TARGETED:
                marker = COMBAT_TARGETED_MARKER
            elif actor.player_state == PlayerState.KO:
                marker = COMBAT_KO_MARKER
            hp_bar = f"{actor.hp}/{actor.max_hp}"
            lines.append(f"{marker} **{actor.name}** | HP: {hp_bar}")
        embed.description = "\n".join(lines) if lines else "Chưa có ai tham gia"
        await msg.edit(embed=embed)


# -------------------- MODAL TẠO ACTOR --------------------
class CreateActorModal(discord.ui.Modal, title="Tạo Actor mới"):
    def __init__(self, gsheet: GoogleSheetsClient, actor_cache: dict, cog: TTRPGCog):
        super().__init__()
        self.gsheet = gsheet
        self.actor_cache = actor_cache
        self.cog = cog

    name_input = discord.ui.TextInput(label="Tên actor", placeholder="Nhập tên nhân vật", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        actor_name = self.name_input.value
        user_id = interaction.user.id
        new_sheet_name = f"actor_{user_id}_{actor_name}"
        success = await self.gsheet.duplicate_sheet("Template", new_sheet_name)
        if not success:
            await interaction.response.send_message("Lỗi tạo actor, thử lại sau!", ephemeral=True)
            return
        index_sheet = await self.gsheet.spreadsheet.worksheet("INDEX")
        await index_sheet.append_row([str(user_id), new_sheet_name, actor_name, ""])
        actor = ActorRuntime(actor_id=new_sheet_name, discord_user_id=user_id, name=actor_name)
        await self.cog._load_actor_from_sheet(actor)
        self.actor_cache[user_id] = actor
        await interaction.response.send_message(f"Đã tạo actor {actor_name}!", ephemeral=True)


# -------------------- HUB VIEW --------------------
class HubView(discord.ui.View):
    def __init__(self, actor: ActorRuntime, evaluator: Evaluator, global_config: dict):
        super().__init__(timeout=60)
        self.actor = actor
        self.evaluator = evaluator
        self.global_config = global_config

    @discord.ui.select(placeholder="Chọn mục", options=[
        discord.SelectOption(label="Profile", value="profile_ui"),
        discord.SelectOption(label="Stat", value="stat_ui"),
        discord.SelectOption(label="Inventory", value="inventory_ui"),
        discord.SelectOption(label="Equipment", value="equipment_ui"),
        discord.SelectOption(label="Skill", value="skill_ui"),
    ])
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        key = select.values[0]
        formula = self.global_config.get(key, "Không có dữ liệu")
        self.evaluator.reset_context()
        self.evaluator.current_actor = self.actor
        self.evaluator.in_combat = False
        try:
            ast = parse(formula)
            result = await self.evaluator.evaluate(ast)
            embed = discord.Embed(title=f"{key}", description=str(result), color=0x2ecc71)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Lỗi hiển thị: {e}", ephemeral=True)


# -------------------- SETUP --------------------
async def setup(bot: commands.Bot, gsheet_client: GoogleSheetsClient):
    cog = TTRPGCog(bot, gsheet_client)
    # Đăng ký các group lệnh vào bot
    bot.tree.add_command(cog.combat_group)
    bot.tree.add_command(cog.macro_group)
    bot.tree.add_command(cog.npc_group)
    bot.tree.add_command(cog.admin_group)
    # Lệnh hub riêng (không group)
    bot.tree.add_command(cog.hub)
    await bot.add_cog(cog)