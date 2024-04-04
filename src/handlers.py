from sqlite3 import Row
from loguru import logger
from openai import AsyncOpenAI

import ai_stuff
from base import ChatInfo, Prompt, UserInfo, Conversation, Message
from constants import AI_EMOJI, HELP_MSG, SYSTEM_EMOJI
from db import (
    create_mood, get_all_moods, get_mood, get_user_mood, get_user_created_moods, is_registered, create_account, update_value, update_mood_value
)
from utils import moderate_query, moderate_result, process_instructions


async def handle_start(user_id: int) -> tuple[str, bool]:
    # bool means if kbd should be returned ot not
    if user_id < 0:
        # ? Does TG works the same way?
        # Groups can't have an account
        return (
            f"{SYSTEM_EMOJI} Нет, ботёнок, для создания аккаунта ты должен быть человеком!", False
        )

    if (await is_registered(user_id)):
        # Person is already registered
        return (f"{SYSTEM_EMOJI} Гений, у тебя уже есть аккаунт в боте. Смирись с этим.", False)

    await create_account(user_id)
    return (f"{SYSTEM_EMOJI} Аккаунт готов; теперь вы можете настраивать поведение бота!", True)


def handle_help() -> str:
    return HELP_MSG


def handle_tokenize(query: str | None = None) -> str:
    if query is None:
        return f"{SYSTEM_EMOJI} Эээ... А что токенизировать то?"

    num_tokens = ai_stuff.num_tokens_from_string(query)

    ending = ('' if num_tokens == 1 else 'а' if num_tokens < 5 else 'ов')
    cost = num_tokens/1000*0.0015
    cost_rounded = "{:.5f}".format(cost)
    return f"{SYSTEM_EMOJI} В сообщении {num_tokens} токен{ending} (${cost_rounded})!"


async def handle_ai(
    client: AsyncOpenAI,
    query: str,
    user: UserInfo,
    reply_user: UserInfo | None = None,
    reply_query: str | None = None,
    chat_info: ChatInfo | None = None,
):
    conv = Conversation([Message(query, str(user.user_id), user.full_name)])

    if reply_user:
        reply_full_name = reply_user.full_name or "Anonymous"
        conv.prepend(
            Message(
                reply_query,
                str(reply_user.user_id),
                reply_full_name
            )
        )

    conversation_text = conv.render(incl_full_name=False)

    fail_reason = await moderate_query(conversation_text, client)
    if fail_reason:
        return fail_reason

    try:
        user_mood = await get_user_mood(user.user_id)
    except TypeError:
        # User is a group or he doesn't have an account
        # Defaulting to assistant mood
        user_mood = await get_mood(0)

    user_mood_instr = user_mood[5]
    mood_instr = process_instructions(
        user_mood_instr,
        (user if reply_user is None else None),
        chat_info
    )

    prompt = Prompt(
        header=Message(mood_instr),
        convo=conv
    )
    response = await ai_stuff.create_response(client, prompt)
    logger.info(response)

    moderated = moderate_result(response)
    if moderated[0] == 1:
        return moderated[1]

    response = moderated[1]
    msg_reply = f"{AI_EMOJI} {response}"

    return msg_reply


async def handle_settings(user_id: int) -> tuple[str, bool]:
    if not (await is_registered(user_id)):
        return (f"{SYSTEM_EMOJI} Для этого надо зарегестрироваться!", False)

    user_mood = await get_user_mood(user_id)
    logger.info(user_mood)
    mood_id = user_mood[0]
    mood_name = user_mood[3]

    return (f"{SYSTEM_EMOJI} Текущий муд: {mood_name} (id: {mood_id})", True)


async def handle_mood_list() -> str:
    moods = await get_all_moods(public_only=True)
    if len(moods) == 0:
        return f"{SYSTEM_EMOJI} Публичных мудов в боте пока не существует!"

    all_moods_str = f"{SYSTEM_EMOJI} Вот все текущие публичные муды:"
    for mood in moods:
        mood_id = mood[0]
        mood_name = mood[3]
        all_moods_str += f"\n• {mood_name} (id: {mood_id})"
    return all_moods_str


async def mood_exists(user_id: int, mood_id: int) -> str | Row:
    mood = await get_mood(mood_id)
    if not mood or (mood[2] == 0 and mood[1] != user_id):
        return f"{SYSTEM_EMOJI} Айди с таким мудом не существует или он приватный!"
    return mood


async def handle_mood_info(mood, full_name: str | None = None) -> tuple[str, int]:
    mood_id, mood_creator_id, _, mood_name, mood_desc, mood_instr = mood
    if full_name:
        mood_by = f"[id{mood_creator_id}|{full_name}]"
    else:
        mood_by = "пользователя"

    return (
        f"{SYSTEM_EMOJI} Муд от {mood_by} - id: {mood_id}"
        f"\n👤 | Имя: {mood_name}"
        f"\n🗒 | Описание: {mood_desc or '<Нету>'}"
        f"\n🤖 | Инструкции: {mood_instr}"
    )


async def handle_set_mood(user_id: int, mood_id: int) -> str:
    if not (await is_registered(user_id)):
        return f"{SYSTEM_EMOJI} Для этого надо зарегестрироваться!"

    custom_mood = await get_mood(mood_id)
    if not custom_mood or (custom_mood[2] == 0 and user_id != custom_mood[1]):
        return f"{SYSTEM_EMOJI} Такого муда не существует!"
    mood_id = custom_mood[0]
    mood_name = custom_mood[3]

    await update_value(user_id, "selected_mood_id", mood_id)
    return f"{SYSTEM_EMOJI} Вы успешно выбрали муд \"{mood_name}\" (id: {mood_id})"


def handle_create_mood_info(cp: str = "!") -> str:
    return (
        f"{SYSTEM_EMOJI} Чтобы создать новый муд,"
        f" напишите \"{cp}создать муд <инструкции>\""
        "\nИнструкции лучше всего писать на английском!"
        "\nНапример: You are now a cute anime girl. Don't forget to use :3 and other things"
        " that cute anime girls say. Speak only Russian."
    )


async def handle_create_mood(client: AsyncOpenAI, user_id: str, instr: str, cp: str = "!") -> str:
    if not (await is_registered(user_id)):
        return (
            f"{SYSTEM_EMOJI} Гений, чтобы создать муд,"
            f" нужно сначала зарегаться командой \"{cp}начать\"."
        )

    fail_reason = await moderate_query(instr, client)
    if fail_reason:
        return fail_reason

    user_moods = await get_user_created_moods(user_id)
    if len(user_moods) >= 5 and user_id != 322615766:  # ! hardcoded
        return f"{SYSTEM_EMOJI} Вы не можете создать больше 5 мудов!"

    # Creating mood
    inserted_id = await create_mood(user_id, "Мой муд", instr)

    # Adding new mood to this user's created moods
    user_moods.append(inserted_id)
    user_moods = [str(i) for i in user_moods]
    await update_value(user_id, "created_moods_ids", ','.join(user_moods))

    # TODO: Make a keyboard for choosing the just created mood

    return (
        f"{SYSTEM_EMOJI} Вы создали новый муд! Его айди: {inserted_id}"
        "\nТеперь вы можете:"
        f"\n1. Поменять название, с помощью команды \"{cp}муд имя {inserted_id} <название муда>\"."
        "\n2. Поменять описание, с помощью команды"
        f" \"{cp}муд описание {inserted_id} <описание муда>\"."
        f"\n3. Сделать муд публичным, с помощью команды \"{cp}муд видимость {inserted_id}\"."
        "\n4. Поменять его инструкции, если вам что-то не понравилось в них."
        f" Команда: \"{cp}муд инструкции {inserted_id} <инструкции>\""
    )


async def handle_edit_mood(client: AsyncOpenAI, user_id: int, params_str: str, cp: str = "!") -> str:
    if not (await is_registered(user_id)):
        return (
            f"{SYSTEM_EMOJI} Что ты там менять собрался? У тебя даже аккаунта нет!"
            f"\n... Поэтому можешь его создать, с помощью команды \"{cp}начать\"."
        )
    params = params_str.split()
    logger.info(f"Got these params: {params}")
    try:
        mood_id = int(params[1])
    except (KeyError, ValueError):
        return (
            f"{SYSTEM_EMOJI} Ты чет не то написал, броу!"
            "\nДоступные параметры: имя, описание, видимость"
        )

    user_moods = await get_user_created_moods(user_id)
    if mood_id not in user_moods:
        return f"{SYSTEM_EMOJI} Гений, это не твой муд! Сделай его копию и меняй как хочешь."

    success_msg = ""
    if params[0] in ("имя", "название"):
        mood_name = ' '.join(params[2:])
        fail_reason = await moderate_query(mood_name)
        if fail_reason:
            return fail_reason

        await update_mood_value(mood_id, "name", mood_name)
        success_msg = "Вы успешно поменяли название муда!"
    elif params[0] == "описание":
        mood_desc = ' '.join(params[2:])
        fail_reason = await moderate_query(mood_desc)
        if fail_reason:
            return fail_reason

        await update_mood_value(mood_id, "desc", mood_desc)
        success_msg = "Вы успешно поменяли описание муда!"
    elif params[0] == "видимость":
        mood = await get_mood(mood_id)
        visibility = mood[2]

        new_visibility = 1
        if visibility == 1:
            new_visibility = 0
        visibility_status = ('публичный' if new_visibility else 'приватный')

        await update_mood_value(mood_id, "visibility", new_visibility)
        success_msg = f"Вы успешно поменяли видимость муда на \"{visibility_status}\""
    elif params[0] == "инструкции":
        mood_instr = ' '.join(params[2:])
        fail_reason = await moderate_query(mood_instr, client)
        if fail_reason:
            return fail_reason

        await update_mood_value(mood_id, "instructions", mood_instr)
        success_msg = "Вы успешно поменяли инструкции муда!"
    else:
        return f"{SYSTEM_EMOJI} Эээ... Что? Такого параметра нету, уж извини!"
    return SYSTEM_EMOJI + " " + success_msg


async def handle_my_moods(user_id: int, cp: str = "!") -> str:
    if not (await is_registered(user_id)):
        return (
            f"{SYSTEM_EMOJI} Гений, чтобы сделать муд,"
            f" нужно сначала зарегаться командой \"{cp}начать\"."
        )

    user_moods = await get_user_created_moods(user_id)
    if len(user_moods) == 0:
        return (
            f"{SYSTEM_EMOJI} Удивительно, но вы ещё не создавали собственный муд!"
            "\nЧтобы его создать, напишите \"{cp}создать муд\""
        )

    user_moods_message = f"{SYSTEM_EMOJI} Ваши муды:"
    for mood in user_moods:
        pub_mood = await get_mood(mood)
        user_moods_message += f"\n• {pub_mood[3]} (id: {pub_mood[0]})"

    return user_moods_message


async def handle_del_account(user_id: int) -> str:
    if not (await is_registered(user_id)):
        return (
            f"{SYSTEM_EMOJI} Пока мы живем в 2024, этот гений живет в 1488"
            "\nУ вас и так нет аккаунта. Отличная причина создать его!"
        )
    await delete_account(user_id)
    return f"{SYSTEM_EMOJI} Готово... но зачем?"