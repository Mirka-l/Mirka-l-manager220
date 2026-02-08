import os
import random
from typing import get_args
import asyncio

import discord
from discord import app_commands, Embed, Color
from discord.ext import tasks
# Includes a lot of other internal libs
from api.central import Central, Player
from common.discord_helpers import *
from db.connect import connect_database

from redis import asyncio as aioredis
from github import Github, GithubIntegration

# Setting up config
with open("config.toml", "rb") as file:
    config = tomllib.load(file)

os.makedirs("logs", exist_ok=True)
logging.basicConfig(level=config["log_level"], filename="logs/ss220.log", filemode="a+",
                    format="%(asctime)s %(levelname)s %(message)s", force=True)

HEAD_ADMIN_ROLES = config["discord"]["roles"]["heads"]
PRIME_ADMIN_ROLES = [*HEAD_ADMIN_ROLES] + \
    config["discord"]["roles"]["prime_admins"]
ADMIN_ROLES = [*HEAD_ADMIN_ROLES] + config["discord"]["roles"]["admins"]
MENTOR_ROLES = [*ADMIN_ROLES] + config["discord"]["roles"]["mentors"]
XENOMOD_ROLES = [*HEAD_ADMIN_ROLES] + config["discord"]["roles"]["xenomod"]
DEV_ROLES = [*HEAD_ADMIN_ROLES] + config["discord"]["roles"]["devs"]
MISC_ROLES = config["discord"]["roles"]["servers"]

CODER_ID = config["discord"]["mentions"]["coder"]

CHANNEL_CACHE: dict[str, discord.TextChannel] = {}

OUR_SERVERS = load_servers_config(config)

server_choices = [app_commands.Choice(
    name=server.name, value=i) for i, server in enumerate(OUR_SERVERS)]

server_type_choices = Literal[*config["central"]["server_types"]]

NO_MENTIONS = discord.AllowedMentions(roles=False, users=False, everyone=False)
last_status_sever = 0

# Setting up db connection
DB: Paradise = connect_database("paradise", config["db"]["paradise"])
REDIS = aioredis.from_url(config["redis"]["connection_string"])
REDIS_SUB = REDIS.pubsub(ignore_subscribe_messages=True)
REDIS_SUB_BINDINGS = {}
CENTRAL = Central(config["central"]["endpoint"],
                  config["central"]["bearer_token"],
                  config["central"]["boosty_discord_id"])


def run_bot():
    intents = discord.Intents.default()
    intents.members = True
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.errors.MissingAnyRole | discord.app_commands.errors.MissingRole):
            await interaction.response.send_message("Рано еще тебе такое использовать.")
        elif isinstance(error, discord.app_commands.errors.NoPrivateMessage):
            await interaction.response.send_message("Не работает в лс.")
        elif isinstance(error, app_commands.CommandInvokeError):
            await interaction.response.send_message("Скорее всего, у меня недостаточно прав в этом канале.")
        else:
            logging.error("%s: %s", type(error), error)
            await interaction.followup.send(f"Что то явно пошло не так. Сообщите об ошибке кодеру(<@{CODER_ID}>)")

    tree.on_error = on_tree_error

    @tree.command(name="пинг", description="Проверить работоспособность бота.")
    async def ping(interaction: discord.Interaction):
        await interaction.response.defer()
        await interaction.followup.send("Понг!")

    # region BYOND Topics

    @tree.command(name="онлайн", description="Показать онлайн серверов.")
    async def online(interaction: discord.Interaction):
        await interaction.response.defer()
        await interaction.followup.send(get_beautified_status(OUR_SERVERS))

    @tree.command(name="админы", description="Показать админов онлайн.")
    async def admins(interaction: discord.Interaction):
        await interaction.response.defer()
        await interaction.followup.send(get_admins(OUR_SERVERS))

    @tree.command(name="кто", description="Показать игроков онлайн.")
    @app_commands.describe(server="Игроков какого сервера показать.")
    @app_commands.choices(server=server_choices)
    async def who(interaction: discord.Interaction, server: app_commands.Choice[int]):
        await interaction.response.defer()
        await interaction.followup.send(get_players_online(OUR_SERVERS[server.value]))

    @tree.command(name="сообщение", description="Отправить ПМку игроку.")
    @app_commands.describe(server="На каком сервере игрок.")
    @app_commands.choices(server=server_choices)
    @app_commands.describe(ckey="Игрок, который получит сообщение.")
    @app_commands.describe(msg="Сообщение.")
    @app_commands.checks.has_any_role(*ADMIN_ROLES)
    async def send_admin_pm(interaction: discord.Interaction, server: app_commands.Choice[int], ckey: str, msg: str):
        await interaction.response.defer()
        await interaction.followup.send(str(OUR_SERVERS[server.value].send_admin_msg(ckey,
                                                                                     msg,
                                                                                     interaction.user.name)))

    @tree.command(name="анонс", description="Сделать анонс от имени хоста.")
    @app_commands.describe(server="Сервер для анонса.")
    @app_commands.choices(server=server_choices)
    @app_commands.describe(msg="Сообщение.")
    @app_commands.checks.has_any_role(*HEAD_ADMIN_ROLES)
    async def make_host_announce(interaction: discord.Interaction, server: app_commands.Choice[int], msg: str):
        await interaction.response.defer()
        OUR_SERVERS[server.value].send_host_announce(msg)
        await interaction.followup.send("Анонс был совершен~~, наверное.~~")

    @tree.command(name="дебаг", description="Получить сырые данные.")
    @app_commands.checks.has_any_role(*HEAD_ADMIN_ROLES)
    async def debug(interaction: discord.Interaction):
        await interaction.response.defer()
        await interaction.followup.send("Дебаг данные:")
        for server in OUR_SERVERS:
            await interaction.channel.send(f"**{server.name}:**\n{server.get_server_status().raw_data}")

    @tasks.loop(seconds=60)
    async def announce_loop():
        try:
            global last_status_sever
            server_info = OUR_SERVERS[last_status_sever].get_server_status()
            pres = f"{OUR_SERVERS[last_status_sever].name}: {server_info.players_num} [{server_info.round_duration}]"
            await client.change_presence(activity=discord.Game(name=pres))
            last_status_sever += 1
            if last_status_sever > len(OUR_SERVERS) - 1:
                last_status_sever = 0

        except Exception as error:
            logging.error(error)

        while True:
            message: dict[bytes] = await REDIS_SUB.get_message(timeout=1.0)
            if not message:
                break
            message_channel = message["channel"].decode()
            if message_channel not in REDIS_SUB_BINDINGS:
                logging.warning(
                    "Got redis event from a channel without handler: %s", message_channel)
                continue
            asyncio.create_task(REDIS_SUB_BINDINGS[message_channel](message))

    # endregion
    # region API & DB

    def get_player_info_embed(player_links_info: Player | None):
        if not player_links_info:
            return embed_player_info(None, None, [])
        ingame_player_info = DB.get_player(player_links_info.ckey)
        chars = DB.get_characters(player_links_info.ckey)
        embed_msg = embed_player_info(
            ingame_player_info, player_links_info, chars)
        return embed_msg

    @tree.command(name="персонаж", description="Узнать сикей по персонажу.")
    @app_commands.describe(name="Имя.")
    @app_commands.checks.has_any_role(*ADMIN_ROLES)
    async def char(interaction: discord.Interaction, name: str):
        await interaction.response.defer()
        embed_msg = Embed(
            title=f"Персонажи по запросу {name}",
            color=Color.blue())
        characters = DB.get_characters_by_name(name)
        for character in characters[:24]:
            embed_msg.add_field(name=character.real_name, value=character.ckey)
        await interaction.followup.send(embed=embed_msg)

    @tree.command(name="баны", description="Баны по сикею.")
    @app_commands.describe(ckey="Сикей админа или игрока.")
    @app_commands.describe(num="Количество банов.")
    @app_commands.checks.has_any_role(*ADMIN_ROLES)
    async def bans(interaction: discord.Interaction, ckey: str, num: int):
        await interaction.response.defer()
        embeds = get_nice_bans(DB.get_bans(ckey))[:num] or [Embed(title="**Отсутствуют баны, связанные с эти игроком.**",
                                                                  color=Color.green())]
        for embed in embeds:
            await interaction.channel.send(embed=embed)
        await interaction.followup.send(f"Список банов **{ckey}**:")

    @tasks.loop(seconds=360)
    async def announceloop_long():
        logging.debug("Starting sending bans.")
        embeds = get_nice_bans(DB.get_recent_bans())
        if not embeds:
            logging.debug("No new bans")
            return
        logging.debug(f"Sending {len(embeds)} bans to banned.")
        for embed in embeds:
            await CHANNEL_CACHE.get("ban").send(embed=embed)
        logging.info(f"Sent {len(embeds)} bans to discord.")

    @tree.command(name="нотесы", description="Нотесы по сикею.")
    @app_commands.describe(ckey="Сикей игрока.")
    @app_commands.describe(num="Количество нотесов.")
    @app_commands.checks.has_any_role(*ADMIN_ROLES)
    async def show_notes(interaction: discord.Interaction, ckey: str, num: int):
        await interaction.response.defer()
        notes = DB.get_notes(ckey, num)
        embeds = embed_notes(notes) or [Embed(title="**Отсутствуют нотесы, связанные с эти игроком.**",
                                              color=Color.green())]
        await interaction.followup.send(f"Список нотесов **{ckey}**:")
        for embed in embeds:
            await interaction.channel.send(embed=embed)
        
    @tree.command(name="рестарт", description="Управление процессом бота.")
    @app_commands.describe(action="0 - Перезагрузить бота, 1 - Полностью выключить")
    async def restart(interaction: discord.Interaction, action: int):
        if action == 0:
            await interaction.response.send_message("**Выполняю перезагрузку...**", ephemeral=False)
            print("\n[SYSTEM] Перезагрузка по команде пользователя...")
            # Выходим с кодом 0, батник подхватит и запустит снова
            os._exit(0)
        
        elif action == 1:
            await interaction.response.send_message("**Выключение...**")
        
            # Чтобы батник не перезапустил бота, нам нужно либо "сломать" цикл, 
            # либо просто закрыть бота и вручную закрыть окно. 
            # Самый надежный способ - остановить клиент и не выходить из скрипта (зависнуть), 
            # либо просто выйти и быстро закрыть окно CMD.
            
            # ПРАВКА: Принудительно закрываем процесс CMD (батник), в котором запущен бот
            print("\n[SYSTEM] Завершение работы...")
            os.system("taskkill /F /T /PID %d" % os.getppid())
            await interaction.client.close()
        
    @tree.command(name="sync", description="Sync.")
    async def sync(interaction: discord.Interaction):
        # Публичное сообщение, чтобы все видели статус
        await interaction.response.defer(ephemeral=False)
    
        try:
            print(f"[SYSTEM] Начата полная синхронизация для сервера: {interaction.guild.name}")
        
            # 1. Сначала очищаем локальные команды этого сервера, чтобы не было дублей
            tree.clear_commands(guild=interaction.guild)
            await tree.sync(guild=interaction.guild)
        
            # 2. Теперь синхронизируем глобальные команды
            # Это "протолкнет" изменения во весь Discord
            synced = await tree.sync()
        
            await interaction.followup.send(
                f"**Синхронизация завершена!**\n"
                f"Synced (**{len(synced)}** commands).\n"
            )
        
        except Exception as e:
            print(f"[ERROR] Ошибка синхронизации: {e}")
            await interaction.followup.send(f"Error {e}")
            
    @tree.command(name="привязать", description="Привязка игрового аккаунта.")
    @app_commands.describe(key="Ваш игровой CKey")
    async def link_public(interaction: discord.Interaction, key: str):
        # 1. Говорим Дискорду, что мы начали работу
        await interaction.response.defer(ephemeral=False)
    
        user = interaction.user
        print(f"[DEBUG] Запущена команда для {user.name}, ключ: {key}")

        try:
        # 2. Вызываем логику API
            result = await CENTRAL.link_player(key, user.id)
            print(f"[DEBUG] Получен ответ от API: {result}")

            if result["status"] == "success":
                embed = discord.Embed(
                    title="🔗 Аккаунт успешно привязан",
                    description=f"Аккаунт `{key}` теперь связан с {user.mention}",
                    color=discord.Color.green()
                )
                embed.set_thumbnail(url=user.display_avatar.url)
                embed.set_footer(text="Синхронизация SSCentral")
            
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send(f"**Ошибка:** {result['message']}")

        except Exception as e:
            print(f"[CRITICAL ERROR] Ошибка в команде привязать: {e}")
            await interaction.followup.send("Произошла критическая ошибка при обработке запроса.")
        
    # --- КУДОСЫ (РЕПУТАЦИЯ) ---

    @tree.command(name="рейтинг", description="ТОП-10 игроков по кудосам.")
    async def rating(interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            # Запрос к API вместо БД
            top_data = await CENTRAL.get_kudos_rating(limit=10)
            
            if not top_data:
                await interaction.followup.send("Рейтинг пока пуст.")
                return
            
            # Ищем ckey автора для подсветки
            user_info = await CENTRAL.get_player_by_discord(interaction.user.id)
            user_ckey = user_info.ckey if user_info else None
            
            description = ""

            for i, entry in enumerate(top_data, 1):
                ckey = entry.get('ckey', 'Unknown')
                score = entry.get('score', 0.0)
                
                # Чистый номер без медалей
                rank_display = f"` {i}. `"
                
                # Подсветка вызвавшего игрока жирным
                name_display = f"**{ckey}**" if ckey == user_ckey else f"{ckey}"
                
                # Красивое форматирование числа
                formatted_score = f"{score:.2f}".rstrip('0').rstrip('.')
                
                description += f"{rank_display} {name_display:15} — **{formatted_score}** ⭐\n"
            
            embed = discord.Embed(
                title="🏆 Таблица лидеров репутации", 
                color=0xFFD700,
                description=description,
                timestamp=discord.utils.utcnow()
            )
            embed.set_footer(text="Данные SSCentral • Обновляется в реальном времени")
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logging.error(f"Rating error: {e}")
            await interaction.followup.send("Не удалось загрузить рейтинг.")

    @tree.command(name="чек_кудосов", description="История похвал (для админов).")
    # @app_commands.checks.has_any_role(*ADMIN_ROLES)
    async def check_kudos(interaction: discord.Interaction, ckey: str):
        await interaction.response.defer()
        try:
            history = await CENTRAL.admin_check_kudos(ckey)
            if not history:
                await interaction.followup.send(f"История для `{ckey}` не найдена.")
                return

            log_text = ""
            for h in history:
                # Парсим дату
                dt = datetime.fromisoformat(h['timestamp'].replace('Z', '+00:00'))
                date_str = dt.strftime("%d.%m %H:%M")
                
                # Собираем строку без поинтов
                log_text += f" `{date_str}` | От: `{h['giver']:12}` | Р: `{h['round_id']}`\n"

            embed = discord.Embed(
                title=f"📜 Логи репутации: {ckey}", 
                description=log_text[:4000], 
                color=0x5865F2,
                timestamp=discord.utils.utcnow()
            )
            embed.set_footer(text=f"Всего записей: {len(history)}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logging.error(f"Check kudos error: {e}")
            await interaction.followup.send(f"Ошибка при получении логов.")

    @tree.command(name="кудос", description="Показать вашу репутацию.")
    async def kudos_me(interaction: discord.Interaction):
        await interaction.response.defer()
        
        try:
            # Всегда берем discord_id того, кто нажал на команду
            target_id = interaction.user.id
            data = await CENTRAL.get_player_kudos_stats(discord_id=target_id)

            if not data:
                await interaction.followup.send("Данные не найдены.")
                return

            score = data.get('total_score', 0.0)
            position = data.get('position', 0)
            next_score = data.get('next_player_score')
            target_ckey = data.get('receiver', "Неизвестно")

            # --- ТВОЙ ФИРМЕННЫЙ ВИЗУАЛ (БЕЗ ИЗМЕНЕНИЙ) ---
            embed = discord.Embed(title=f"🏆 Репутация игрока: {target_ckey}", timestamp=discord.utils.utcnow())
            bar_length = 10
            display_score = f"{score:.2f}".rstrip('0').rstrip('.')

            if score > 0:
                if position == 1:
                    embed.color = discord.Color.gold()
                    bar = "🟧" * bar_length
                    progress_text = "**Вы лидер рейтинга!**"
                else:
                    embed.color = discord.Color.from_rgb(46, 204, 113)
                    if next_score and next_score > score:
                        diff = next_score - score
                        percent = score / next_score
                        filled = min(max(1, round(percent * bar_length)), bar_length - 1)
                        bar = "🟩" * filled + "⬜" * (bar_length - filled)
                        progress_text = f"До следующего места: **{f'{diff:.2f}'.rstrip('0').rstrip('.')}** поинтов"
                    else:
                        bar = "🟩" * 9 + "⬜" * 1
                        progress_text = "Вы почти на вершине!"
            else:
                embed.color = discord.Color.light_gray()
                bar = "⬜" * bar_length
                progress_text = "Не в рейтинге."
                position = "—"

            embed.add_field(name="⭐ Очки похвал", value=f"**{display_score}**\n{bar}\n{progress_text}", inline=False)
            embed.add_field(name="🏅 Место в ТОП", value=f"**#{position}**" if score > 0 else "**Вне рейтинга**", inline=True)

            if interaction.user.display_avatar:
                embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.set_footer(text=f"Запросил: {interaction.user.display_name}")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logging.error(f"Kudos error: {e}")
            await interaction.followup.send("Произошла ошибка при получении данных.")

    # region Xenowl

    @tree.command(name="добавить_вайтлист_на_ксенорасу", description="Разрешить игроку играть на указанной ксенорасе")
    @app_commands.describe(ckey="Сикей.")
    @app_commands.describe(specie="Ксенораса.")
    @app_commands.checks.has_any_role(*XENOMOD_ROLES)
    async def add_specie_to_whitelist(interaction: discord.Interaction, ckey: str, specie: ALL_PLAYABLE_SPECIES):
        await interaction.response.defer()
        result = ""
        species_whitelist_response = DB.get_player_species_whitelist(ckey)
        if not species_whitelist_response:
            result = f"Не найден игрок с сикеем {ckey}"
        else:
            species_whitelist = json.loads(species_whitelist_response[0])

            if specie not in species_whitelist:
                species_whitelist.append(specie)
                result = f"Игрок с сикеем {ckey} получил вайтлист на расу {specie}"
                match DB.set_player_species_whitelist(ckey, json.dumps(species_whitelist)):
                    case ERRORS.ERR_404:
                        result = "Что-то пошло не так"
            else:
                result = f"У игрока {ckey} уже есть вайтлист на расу {specie}"

        await interaction.followup.send(result)

    @tree.command(name="убрать_вайтлист_на_ксенорасу", description="Отобрать у игрока вайтлист к указанной ксенорасе")
    @app_commands.describe(ckey="Сикей.")
    @app_commands.describe(specie="Ксенораса.")
    @app_commands.checks.has_any_role(*XENOMOD_ROLES)
    async def remove_specie_from_whitelist(interaction: discord.Interaction, ckey: str, specie: ALL_PLAYABLE_SPECIES):
        await interaction.response.defer()
        result = ""
        species_whitelist_response = DB.get_player_species_whitelist(ckey)
        if not species_whitelist_response:
            result = f"Не найден игрок с сикеем {ckey}"
        else:
            species_whitelist = json.loads(species_whitelist_response[0])

            if specie in species_whitelist:
                species_whitelist.remove(specie)
                result = f"Игрок с сикеем {ckey} потерял вайтлист на расу {specie}"
                match DB.set_player_species_whitelist(ckey, json.dumps(species_whitelist)):
                    case ERRORS.ERR_404:
                        result = "Что-то пошло не так"
            else:
                result = f"У игрока {ckey} уже нет вайтлиста на расу {specie}"

        await interaction.followup.send(result)

    @tree.command(name="очистить_вайтлист", description="Отобрать у игрока все вайтлисты на расы")
    @app_commands.describe(ckey="Сикей.")
    @app_commands.checks.has_any_role(*XENOMOD_ROLES)
    async def remove_all_species_from_whitelist(interaction: discord.Interaction, ckey: str):
        await interaction.response.defer()
        result = ""
        species_whitelist_response = DB.get_player_species_whitelist(ckey)
        if not species_whitelist_response:
            result = f"Не найден игрок с сикеем {ckey}"
        else:
            result = f"Игрок {ckey} потерял вайтлист на все расы, кроме человека"
            match DB.set_player_species_whitelist(ckey, "[\"Human\"]"):
                case ERRORS.ERR_404:
                    result = "Что-то пошло не так"

        await interaction.followup.send(result)

    @tree.command(name="дать_вайтлист_на_все_расы", description="Дать игроку вайтлист на все расы")
    @app_commands.describe(ckey="Сикей.")
    @app_commands.checks.has_any_role(*XENOMOD_ROLES)
    async def grant_all_species_to_player(interaction: discord.Interaction, ckey: str):
        await interaction.response.defer()
        result = ""
        species_whitelist_response = DB.get_player_species_whitelist(ckey)
        if not species_whitelist_response:
            result = f"Не найден игрок с сикеем {ckey}"
        else:
            result = f"Игрок {ckey} получил вайтлист на все расы"
            all_species = ", ".join(
                f'"{specie}"' for specie in get_args(ALL_PLAYABLE_SPECIES))
            match DB.set_player_species_whitelist(ckey, f"[{all_species}]"):
                case ERRORS.ERR_404:
                    result = "Что-то пошло не так"

        await interaction.followup.send(result)

    # endregion

    # endregion
    # region Central

    @tree.command(name="я", description="Посмотреть информацию о себе.")
    async def me(interaction: discord.Interaction):
        await interaction.response.defer()
        player_links_info = await CENTRAL.get_player_by_discord(interaction.user.id)
        embed_msg = get_player_info_embed(player_links_info)
        await interaction.followup.send(embed=embed_msg)

    @tree.command(name="дискорд", description="Посмотреть информацию об игроке по дискорду.")
    @app_commands.describe(player_discord_user="Игрок в дискорде.")
    @app_commands.rename(player_discord_user="игрок")
    @app_commands.checks.has_any_role(*ADMIN_ROLES)
    async def player_by_discord(interaction: discord.Interaction, player_discord_user: discord.Member):
        await interaction.response.defer()
        player_links_info = await CENTRAL.get_player_by_discord(player_discord_user.id)
        embed_msg = get_player_info_embed(player_links_info)
        await interaction.followup.send(embed=embed_msg)

    @tree.command(name="игрок", description="Посмотреть информацию об игроке.")
    @app_commands.describe(ckey="Логин игрока в BYOND.")
    @app_commands.rename(ckey="сикей")
    @app_commands.checks.has_any_role(*ADMIN_ROLES)
    async def player(interaction: discord.Interaction, ckey: str):
        await interaction.response.defer()
        player_links_info = await CENTRAL.get_player_by_ckey(ckey)
        embed_msg = get_player_info_embed(player_links_info)
        await interaction.followup.send(embed=embed_msg)

    @tree.command(name="вайтлисты")
    @app_commands.rename(ckey="сикей")
    @app_commands.rename(player_discord_user="дискорд")
    @app_commands.rename(server_type="сервер")
    @app_commands.rename(active_only="активные")
    @app_commands.checks.has_any_role(*PRIME_ADMIN_ROLES)
    async def get_whitelists(interaction: discord.Interaction, ckey: str | None = None, player_discord_user: discord.Member | None = None, server_type: server_type_choices | None = None, active_only: bool = False):  # type: ignore
        await interaction.response.defer()
        if not (ckey or player_discord_user):
            await interaction.followup.send("Нужно указать хотя бы один идентификатор игрока.")
            return
        whitelists = await CENTRAL.get_player_whitelists(ckey=ckey, discord_id=player_discord_user.id if player_discord_user else None, server_type=server_type, active_only=active_only)

        await interaction.followup.send(embed=embed_player_whitelists(whitelists))

    @tree.command(name="мои_вайтлисты")
    @app_commands.rename(server_type="сервер")
    async def my_whitelists(interaction: discord.Interaction, server_type: server_type_choices | None = None):  # type: ignore
        await interaction.response.defer()
        whitelists = await CENTRAL.get_player_whitelists(discord_id=interaction.user.id, server_type=server_type)
        await interaction.followup.send(embed=embed_player_whitelists(whitelists))

    @tree.command(name="вписать", description="Дать игроку вайтлист. Нужно указать discord_id или ckey.")
    @app_commands.rename(ckey="сикей")
    @app_commands.rename(player_discord_user="дискорд")
    @app_commands.rename(server_type="сервер")
    @app_commands.rename(duration_days="длительность")
    @app_commands.describe(duration_days="Длительность в днях.")
    @app_commands.checks.has_any_role(*PRIME_ADMIN_ROLES)
    async def grant_whitelist(interaction: discord.Interaction, ckey: str | None = None, player_discord_user: discord.Member | None = None, server_type: server_type_choices = "prime", duration_days: int = 30):  # type: ignore
        await interaction.response.defer()
        status, wl = None, None
        if player_discord_user:
            status, wl = await CENTRAL.give_whitelist_discord(player_discord_user.id, interaction.user.id, server_type, duration_days)
        elif ckey:
            player = await CENTRAL.get_player_by_ckey(ckey)
            if not player:
                await interaction.followup.send("Игрок не нашелся.")
                return
            try:
                player_discord_user = await interaction.guild.fetch_member(player.discord_id)
            except discord.NotFound:
                await interaction.followup.send("игрок должен быть на сервере.")
                return
            status, wl = await CENTRAL.give_whitelist_discord(player.discord_id , interaction.user.id, server_type, duration_days)
        else:
            await interaction.followup.send("Нужно указать хотя бы один идентификатор игрока.")
            return
        if status == 409:
            await interaction.followup.send("Игрок выписан из этого типа вайтлиста.")
            return
        await interaction.followup.send(f"Вайтлист #{wl.id} в {server_type} игроку {player_discord_user.mention} на {duration_days} дней успешно выдан.")
        role_to_add = discord.utils.get(
            interaction.guild.roles, id=config["central"]["server_types"][server_type])
        if role_to_add is None:
            await interaction.followup.send("Для данного типа вайтлиста роль не нашлась.")
            return
        await player_discord_user.add_roles(role_to_add)

    @tree.command(name="выписать", description="Выписать игрока из вайтилиста.")
    @app_commands.rename(ckey="сикей")
    @app_commands.rename(player_discord_user="дискорд")
    @app_commands.rename(server_type="сервер")
    @app_commands.rename(duration_days="длительность")
    @app_commands.describe(duration_days="Длительность в днях.")
    @app_commands.checks.has_any_role(*PRIME_ADMIN_ROLES)
    async def whitelist_ban(interaction: discord.Interaction, ckey: str | None = None, player_discord_user: discord.Member | None = None, server_type: server_type_choices = "prime", duration_days: int = 14, reason: str | None = None):  # type: ignore
        await interaction.response.defer()
        player_discord_id = None
        if ckey:
            player = await CENTRAL.get_player_by_ckey(ckey)
            if not player:
                await interaction.followup.send("Игрок не нашелся.")
                return
            player_discord_id = player.discord_id
            try:
                player_discord_user = await interaction.guild.fetch_member(player.discord_id)
            except discord.NotFound:
                pass
        elif player_discord_user:
            player_discord_id = player_discord_user.id    
        else:
            await interaction.followup.send("Нужно указать хотя бы один идентификатор игрока.")
            return
 
        wl_ban = await CENTRAL.ban_whitelist_discord(player_discord_id, interaction.user.id, server_type, duration_days, reason)
        if player_discord_user:
            role_to_remove = discord.utils.get(interaction.guild.roles, id=config["central"]["server_types"][server_type])
            if role_to_remove is None:
                await interaction.followup.send("Для данного типа вайтлиста роль не нашлась.")
            else:
                await player_discord_user.remove_roles(role_to_remove)
                logging.info("Removed role %s from %s", role_to_remove, player_discord_user)
        logging.info("Added wl_ban %s", wl_ban)
        await interaction.followup.send(f"Выписка #{wl_ban.id} из {server_type} игроку {player_discord_user.mention if player_discord_user else ckey} на {duration_days} дней успешно выдана.")

    @tree.command(name="выписки", description="Посмотреть выписки игрока/админа.")
    @app_commands.rename(ckey="сикей")
    @app_commands.rename(player_discord_user="дискорд")
    @app_commands.rename(admin_discord_user="дискорд_админа")
    @app_commands.rename(server_type="сервер")
    @app_commands.rename(active_only="активные")
    @app_commands.rename(amount="количество")
    @app_commands.checks.has_any_role(*PRIME_ADMIN_ROLES)
    async def whitelist_bans(interaction: discord.Interaction, ckey: str | None, player_discord_user: discord.Member | None = None, admin_discord_user: discord.Member | None = None, server_type: server_type_choices | None = None, active_only: bool = False, amount: int = 10):  # type: ignore
        await interaction.response.defer()
        player_discord_id = None
        if ckey:
            player = await CENTRAL.get_player_by_ckey(ckey)
            if not player:
                await interaction.followup.send("Игрок не нашелся.")
                return
            player_discord_id = player.discord_id
        elif player_discord_user:
            player_discord_id = player_discord_user.id
        wl_bans = await CENTRAL.get_whitelist_bans(player_discord_id or None, admin_discord_user.id if admin_discord_user else None, server_type, active_only, amount)
        await interaction.followup.send(
            f"Выписки{f' на {server_type}' if server_type else ''}{f' игрока {player_discord_user.mention}' if player_discord_user else ''}{f' от админа {admin_discord_user.mention}' if admin_discord_user else ''}:",
        )
        for wl_ban in embed_whitelist_bans(wl_bans):
            await interaction.channel.send(embed=wl_ban)

    @tree.command(name="мои_выписки", description="Посмотреть свои выписки.")
    @app_commands.rename(server_type="сервер")
    @app_commands.rename(active_only="активные")
    async def my_whitelist_bans(interaction: discord.Interaction, server_type: server_type_choices | None = None, active_only: bool = False):  # type: ignore
        await interaction.response.defer()
        wl_bans = await CENTRAL.get_whitelist_bans(player_discord_id=interaction.user.id, server_type=server_type, active_only=active_only)
        await interaction.followup.send(
            f"Мои выписки{f' на {server_type}' if server_type else ''}:",
            embeds=embed_whitelist_bans(wl_bans),
        )

    @tree.command(name="развыписать", description="Анулировать выписку игрока.")
    @app_commands.rename(wl_ban_id="номер_выписки")
    @app_commands.checks.has_any_role(*PRIME_ADMIN_ROLES)
    async def whitelist_unban(interaction: discord.Interaction, wl_ban_id: int):
        await interaction.response.defer()
        status, wl_ban = await CENTRAL.pardon_whitelist_ban(wl_ban_id)
        if status == 404:
            await interaction.followup.send("Выписка не найдена.")
            return
        await interaction.followup.send(f"Выписка #{wl_ban.id} успешно анулирована.")

    @client.event
    async def on_member_update(before: discord.Member, after: discord.Member):
        if before.roles == after.roles:
            return

        # TODO: extract to a function handle_role_loss
        negative_delta = set(before.roles) - set(after.roles)
        donate_roles_removed = {role.id for role in negative_delta} & set(
            map(int, config["central"]["donation_roles"].keys()))
        if donate_roles_removed:
            logging.info("User %s lost donate tier role in discord.", after.id)
            await CENTRAL.remove_donate_tiers(after.id)
            await CENTRAL.remove_donate_wls(after.id)

        # TODO: extract to a function handle_role_gain
        delta = set(after.roles) - set(before.roles)
        donate_roles_added = {role.id for role in delta} & set(
            map(int, config["central"]["donation_roles"].keys()))

        if not donate_roles_added:
            return

        donate_tiers = [config["central"]["donation_roles"]
                        [str(role)] for role in donate_roles_added]
        tier_to_give = max(donate_tiers)

        logging.info("User %s got donate tier %s role in discord.",
                     after.id, tier_to_give)
        await CENTRAL.give_donate_tier(after.id, tier_to_give, 7777)

        if tier_to_give < config["central"]["min_donate_tier_wl"]:
            return

        for server_type in config["central"]["donate_gives_server_types"]:

            status, wl = await CENTRAL.give_whitelist_discord(
                after.id,
                config["central"]["boosty_discord_id"],
                server_type,
                7777  # forever
            )

            if status == 409:
                logging.info(
                    "User %s couldnt get wl from donation due to ban", after.id)
                return
            logging.info("User %s got wl %s from donation", after.id, wl.id)

            role_to_add = discord.utils.get(
                after.guild.roles, id=config["central"]["server_types"][server_type])
            if role_to_add is None:
                logging.info(
                    "User %s couldnt get wl from donation due to no role", after.id)
                return
            await after.add_roles(role_to_add)

    async def on_player_link(entry: dict[bytes]):
        player_json = json.loads(entry["data"].decode())
        player = Player(**player_json)
        logging.info("Player link updated: %s", player)
        # guild = await client.fetch_guild(config["discord"]["guild"])
        # player_discord_user = await guild.fetch_member(player.discord_id)
        # if player_discord_user is None:
        #     logging.warning("Player %s isnot on discord", player.ckey)
        #     return
        # await player_discord_user.add_roles(
        #     discord.utils.get(guild.roles, id=config["discord"]["roles"]["linked"]))

    @tasks.loop(hours=1)
    async def wl_role_update_loop():
        logging.info("Updating wl roles")
        guild = await client.fetch_guild(config["discord"]["guild"])

        server_type_to_actual_whitelisted_discord_ids = {
            server_type: await CENTRAL.get_whitelisted_discord_ids(
                server_type,
                active_only=True
            )
            for server_type in config["central"]["server_types"]
        }
        server_type_to_role = {
            server_type: discord.utils.get(
                guild.roles, id=config["central"]["server_types"][server_type]
            )
            for server_type in config["central"]["server_types"]
        }

        async for member in guild.fetch_members(limit=None):
            for server_type in config["central"]["server_types"]:
                role = server_type_to_role[server_type]
                if role is None:
                    logging.error("Role for %s not found", server_type)
                    continue

                if role in member.roles and member.id not in server_type_to_actual_whitelisted_discord_ids[server_type]:
                    await member.remove_roles(role)
                    logging.info(
                        "Removed outdated role for %s from %s", server_type, member.id)
                elif role not in member.roles and member.id in server_type_to_actual_whitelisted_discord_ids[server_type]:
                    await member.add_roles(role)
                    logging.info(
                        "Added role for %s to %s", server_type, member.id)

    # endregion
    # region MISC

    @tree.command(name="ролл", description="Бросить кость.")
    @app_commands.describe(d="Количество граней.")
    @app_commands.describe(action="Действие.")
    async def roll(interaction: discord.Interaction, d: int, action: str):
        await interaction.response.defer()
        if d < 1:
            await interaction.followup.send("<:facepalm:1098305470017589309>")
            return
        result = (
            f"@{interaction.user.display_name} бросает {d}-гранную кость на '{action}',"
            f" и выпадает {random.randint(1, d)}!"
        )
        await interaction.followup.send(result)

    @tree.command(name="мерж", description="Инициировать мерж апстрима")
    @app_commands.describe(build="Билд")
    @app_commands.choices(build=[app_commands.Choice(name=build, value=build) for build in config["workflow"].keys()])
    @app_commands.checks.has_any_role(*HEAD_ADMIN_ROLES)
    async def merge_upstream(interaction: discord.Interaction, build: str):
        await interaction.response.defer()
        workflow_config = config["workflow"][build]

        try:
            with open(workflow_config["private_key_source"], "r") as key_file:
                integration = GithubIntegration(
                    workflow_config["app_id"], key_file.read())
                token = integration.get_access_token(
                    workflow_config["installation_id"]).token
                github = Github(token)
            repo = github.get_repo(workflow_config["repo_id"])
            merge_workflow = repo.get_workflow(
                workflow_config["merge_upstream"])
            if merge_workflow.create_dispatch(workflow_config["ref"]):
                result = (
                    f"Инициирован мерж апстрима {CHECKMARK_ICON}"
                    f"\n-# {build}"
                )
            else:
                result = (
                    f"Что-то пошло не так {MISTAKE_ICON}"
                    f"\n-# {build} - status code error"
                )
        except Exception as e:
            logging.error(e)
            result = (
                f"Что-то пошло не так {MISTAKE_ICON}"
                f"\n-# {build} - exception occurred"
            )

        await interaction.followup.send(result)

    async def publish_news(entry: dict[bytes]):
        logging.info("Got news from redis")
        await asyncio.sleep(config["discord"]["redis"]["news_delay"])
        article = json.loads(entry["data"].decode())
        embed = Embed(title=article["title"], color=Color.random())
        embed.add_field(
            name=f"{article['channel_name']} сообщает", value=article["body"])
        embed.set_footer(
            text=(
                f"{article['author']}\n"
                f"Код - {article['security_level']}, {article['publish_time']} с начала смены\n"
                "\n"
                f"{SERVERS_NICE[article['server']][0]} - {article['round_id']} - {article['author_ckey']}"
            )
        )
        img_file = None
        if article["img"]:
            img_b64 = article["img"]
            img_file = base64_to_discord_image(img_b64)
            embed.set_image(url="attachment://article_photo.png")
        channel = CHANNEL_CACHE.get("news")
        await channel.send(embed=embed, file=img_file, allowed_mentions=NO_MENTIONS)

    # endregion

    @client.event
    async def on_ready():
        await tree.sync()
        await client.change_presence(activity=discord.Game(name="Самое время поднимать парадиз!"))

        for channel in config["discord"]["channels"]:
            CHANNEL_CACHE[channel] = client.get_partial_messageable(
                config["discord"]["channels"][channel])

        await REDIS_SUB.subscribe("byond.news")
        REDIS_SUB_BINDINGS["byond.news"] = publish_news

        await REDIS_SUB.subscribe("central.link")
        REDIS_SUB_BINDINGS["central.link"] = on_player_link

        announce_loop.start()
        announceloop_long.start()
        wl_role_update_loop.start()
        logging.info("Set up SS220 Manager")

    client.run(config["token"])


if __name__ == '__main__':
    run_bot()
