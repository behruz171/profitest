import asyncio
import os
import json
from typing import List, Optional, Dict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from backend import (
    correct_option_for_question,
    get_question_options,
    get_test_questions,
    import_tests_from_data,
    import_tests_from_file,
    init_db,
    is_option_correct,
    list_tests,
    create_session,
    get_active_session,
    session_question_at,
    record_answer,
    stop_session,
    finish_if_done,
    user_results,
    get_session,
)


# BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
# ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}
BOT_TOKEN = "8428673351:AAFCraPsPOteSMmh3NvCKUSzpQ4ZldVEwGA"
ADMIN_IDS = [6550264522]

PENDING_COUNT: Dict[int, int] = {}


def tests_keyboard() -> InlineKeyboardMarkup:
    rows = list_tests()
    kb = InlineKeyboardBuilder()
    for r in rows:
        kb.button(text=r["title"], callback_data=f"choose_test:{r['id']}")
    kb.adjust(1)
    return kb.as_markup()


def render_question(session_id: int, index: int) -> Optional[tuple[str, InlineKeyboardMarkup]]:
    q = session_question_at(session_id, index)
    if not q:
        return None
    options = get_question_options(q["id"])
    sess = get_session(session_id)
    total = len(json.loads(sess["questions_json"])) if sess else 0
    lines: List[str] = []
    lines.append(f"Savol {index + 1}/{total}:")
    lines.append(q["text"])
    lines.append("")
    lines.append("Variantlar:")
    for i, o in enumerate(options, start=1):
        lines.append(f"{i}) {o['text']}")
    text = "\n".join(lines)

    kb = InlineKeyboardBuilder()
    for i, o in enumerate(options, start=1):
        kb.button(text=str(i), callback_data=f"ans:{session_id}:{index}:{o['id']}")
    kb.button(text="⏹️ To'xtatish", callback_data=f"stop:{session_id}")
    kb.adjust(4, 1)
    return text, kb.as_markup()


async def prompt_counts(call: CallbackQuery, test_id: int):
    total = len(get_test_questions(test_id))
    kb = InlineKeyboardBuilder()
    # preset choices
    for c in [5, 10]:
        if c <= total:
            kb.button(text=str(c), callback_data=f"count:{test_id}:{c}")
    kb.button(text="Hammasi", callback_data=f"count:{test_id}:{total}")
    kb.button(text="Boshqa (raqam yuboring)", callback_data=f"count_custom:{test_id}")
    kb.adjust(3, 1)
    await call.message.edit_text(
        f"Nechta savol yechmoqchisiz? (Jami: {total})", reply_markup=kb.as_markup()
    )


async def on_start(message: Message):
    init_db()
    tests = list_tests()
    if not tests:
        await message.answer(
            "Hali testlar mavjud emas. Admin JSON orqali yuklashi kerak. /help ni bosing."
        )
        return
    sess = get_active_session(message.from_user.id) if message.from_user else None
    if sess:
        kb = InlineKeyboardBuilder()
        kb.button(text="Davom etish", callback_data=f"resume:{sess['id']}")
        await message.answer("Testni tanlang:", reply_markup=tests_keyboard())
        await message.answer("Sizda davom etayotgan test bor.", reply_markup=kb.as_markup())
    else:
        await message.answer("Testni tanlang:", reply_markup=tests_keyboard())


async def on_help(message: Message):
    text = (
        "Buyruqlar:\n"
        "/start — testni tanlash\n"
        "/help — yordam\n"
        "Adminlar uchun: JSON faylni yuboring (.json), bot uni import qiladi."
    )
    await message.answer(text)


async def on_select_test(call: CallbackQuery):
    try:
        _, test_id_s = call.data.split(":")
        test_id = int(test_id_s)
    except Exception:
        await call.answer("Xatolik.", show_alert=True)
        return
    await prompt_counts(call, test_id)
    await call.answer()


async def on_answer(call: CallbackQuery):
    # ans:{session_id}:{q_index}:{option_id}
    try:
        _, sid, idx, oid = call.data.split(":")
        session_id = int(sid)
        q_index = int(idx)
        option_id = int(oid)
    except Exception:
        await call.answer("Xatolik.", show_alert=True)
        return

    q = session_question_at(session_id, q_index)
    if not q:
        await call.answer("Mavjud emas.")
        return
    ok = record_answer(session_id, q["id"], option_id)
    corr = correct_option_for_question(q["id"])
    corr_text = corr["text"] if corr else ""
    feedback = "✅ To'g'ri!" if ok else f"❌ Noto'g'ri. To'g'ri javob: {corr_text}"
    await call.answer(feedback, show_alert=False)

    if finish_if_done(session_id):
        sess = get_session(session_id)
        await call.message.edit_text(
            f"Test yakunlandi. Natija: {sess['correct_count']}/{sess['total_answered']}. /start",
        )
        return

    nxt = render_question(session_id, q_index + 1)
    if nxt:
        text, kb = nxt
        await call.message.edit_text(text, reply_markup=kb)
    else:
        await call.message.edit_text("Test yakunlandi. /start")


async def on_admin_json(message: Message, bot: Bot):
    if message.from_user and message.from_user.id not in ADMIN_IDS:
        return
    if not message.document or not message.document.file_name.lower().endswith(".json"):
        return
    tmp_path = os.path.join(os.path.dirname(__file__), "_upload.json")
    await bot.download(message.document, destination=tmp_path)
    try:
        n = import_tests_from_file(tmp_path)
        await message.reply(f"Import qilindi: {n} ta test.")
    except Exception as e:
        await message.reply(f"Import xato: {e}")
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


async def on_choose_count(call: CallbackQuery):
    # count:{test_id}:{count}
    try:
        _, tid, cnt = call.data.split(":")
        test_id = int(tid)
        count = int(cnt)
    except Exception:
        await call.answer("Xatolik.", show_alert=True)
        return
    user_id = call.from_user.id if call.from_user else 0
    old = get_active_session(user_id)
    if old:
        stop_session(old["id"])
    session_id = create_session(user_id, test_id, count)
    first = render_question(session_id, 0)
    if not first:
        await call.message.edit_text("Savol topilmadi.")
    else:
        text, kb = first
        await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


async def on_choose_custom(call: CallbackQuery):
    # count_custom:{test_id}
    try:
        _, tid = call.data.split(":")
        test_id = int(tid)
    except Exception:
        await call.answer("Xatolik.", show_alert=True)
        return
    if call.from_user:
        PENDING_COUNT[call.from_user.id] = test_id
    await call.message.edit_text("Raqam yuboring (nechta savol)?")
    await call.answer()


async def on_custom_number(message: Message):
    if not message.from_user or message.from_user.id not in PENDING_COUNT:
        return
    if not message.text or not message.text.isdigit():
        await message.reply("Raqam yuboring.")
        return
    test_id = PENDING_COUNT.pop(message.from_user.id)
    count = int(message.text)
    old = get_active_session(message.from_user.id)
    if old:
        stop_session(old["id"])
    session_id = create_session(message.from_user.id, test_id, count)
    first = render_question(session_id, 0)
    if not first:
        await message.answer("Savol topilmadi.")
    else:
        text, kb = first
        await message.answer(text, reply_markup=kb)


async def on_stop(call: CallbackQuery):
    try:
        _, sid = call.data.split(":")
        session_id = int(sid)
    except Exception:
        await call.answer("Xatolik.")
        return
    stop_session(session_id)
    sess = get_session(session_id)
    await call.message.edit_text(
        f"To'xtatildi. Natija: {sess['correct_count']}/{sess['total_answered']}. /start",
    )
    await call.answer()


async def on_resume(call: CallbackQuery):
    try:
        _, sid = call.data.split(":")
        session_id = int(sid)
    except Exception:
        await call.answer("Xatolik.")
        return
    sess = get_session(session_id)
    if not sess or sess["status"] != "active":
        await call.answer("Sessiya topilmadi.", show_alert=True)
        return
    nxt = render_question(session_id, int(sess["current_index"]))
    if nxt:
        text, kb = nxt
        await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


async def on_results(message: Message):
    if not message.from_user:
        return
    rows = user_results(message.from_user.id, limit=10)
    if not rows:
        await message.answer("Natijalar yo'q.")
        return
    lines = ["Oxirgi natijalar:"]
    for r in rows:
        lines.append(
            f"#{r['id']} — {r['title']}: {r['correct_count']}/{r['total_answered']} ({r['status']})"
        )
    await message.answer("\n".join(lines))


def setup_dispatcher(dp: Dispatcher):
    dp.message.register(on_start, Command("start"))
    dp.message.register(on_help, Command("help"))
    dp.message.register(on_results, Command("results"))
    dp.message.register(on_admin_json, F.document)
    dp.message.register(on_custom_number, F.text)
    dp.callback_query.register(on_select_test, F.data.startswith("choose_test:"))
    dp.callback_query.register(on_choose_count, F.data.startswith("count:"))
    dp.callback_query.register(on_choose_custom, F.data.startswith("count_custom:"))
    dp.callback_query.register(on_answer, F.data.startswith("ans:"))
    dp.callback_query.register(on_stop, F.data.startswith("stop:"))
    dp.callback_query.register(on_resume, F.data.startswith("resume:"))


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    setup_dispatcher(dp)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

