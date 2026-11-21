#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PsyhoTestBot — Telegram Psychodiagnostic Platform
Telegram bot for doctors to manage test sessions and receive results.

Dependencies:
    pip install aiogram==3.7.0 asyncpg python-dotenv aiohttp reportlab
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime
from io import BytesIO

import aiohttp
import asyncpg
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, BotCommand, MenuButtonCommands
)
from aiohttp import web
from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors

load_dotenv()

# ── Configuration ────────────────────────────────────────────

BOT_TOKEN    = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
MINI_APP_URL = os.getenv("MINI_APP_URL")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "127.0.0.1")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
WEBHOOK_PATH = "/result"
ADMIN_TG_ID  = int(os.getenv("ADMIN_TG_ID", "0"))

from report import (generate_pdf_report, parse_details, SCREEN_MAX,
                    item9_alert, severity_ua, gad7_cutoff, item_lines)

TESTS = {
    "pcl5":      {"name": "PCL-5 (ПТСР)",                    "emoji": "🧠"},
    "minmult":   {"name": "Mini-Mult (скорочений MMPI)", "emoji": "📋"},
    "schmishek": {"name": "Шмішек (акцентуації)", "emoji": "🔍"},
    "phq9":      {"name": "PHQ-9 (депресія, скринінг)",      "emoji": "📝"},
    "gad7":      {"name": "GAD-7 (тривога, скринінг)",       "emoji": "📝"},
    "isi": {"name": "ISI (порушення сну)", "emoji": "😴"},
    "phq15": {"name": "PHQ-15 (соматизація)", "emoji": "🌡️"},
    "pss10": {"name": "PSS-10 (рівень стресу)", "emoji": "⚡"},
    "who5": {"name": "WHO-5 (індекс благополуччя)", "emoji": "🌱"},
    "nsi": {"name": "NSI (посткомоційний синдром)", "emoji": "🤕"},
    "brief_cope": {"name": "Brief COPE (копінг-стратегії)", "emoji": "🛡️"},
    "audit_c": {"name": "AUDIT-C (скринінг алкоголю)", "emoji": "🍷"},
    "audit": {"name": "AUDIT (вживання алкоголю)", "emoji": "🍷"},
    "c_ssrs": {"name": "C-SSRS (суїцидальний ризик)", "emoji": "🆘"},
    "express": {"name": "Комплексний експрес-скринінг", "emoji": "📋"},
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger(__name__)

# ── FSM States ───────────────────────────────────────────────

class NewTest(StatesGroup):
    choosing_test = State()
    entering_name = State()
    confirming    = State()
    choosing_sex  = State()

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)
db_pool: asyncpg.Pool = None


# ── Database ─────────────────────────────────────────────────

async def get_db() -> asyncpg.Pool:
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    return db_pool


async def ensure_doctor(telegram_id: int, full_name: str) -> int:
    pool = await get_db()
    row = await pool.fetchrow(
        """
        INSERT INTO doctors (telegram_id, full_name)
        VALUES ($1, $2)
        ON CONFLICT (telegram_id) DO UPDATE SET full_name = EXCLUDED.full_name
        RETURNING id
        """,
        telegram_id, full_name
    )
    return row["id"]


async def create_session(doctor_id: int, patient_name: str, test_type: str, sex=None) -> str:
    pool = await get_db()
    token = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO tokens (token, doctor_id, full_name, test_type, status, sex) VALUES ($1, $2, $3, $4, 'pending', $5)",
        token, doctor_id, patient_name, test_type, sex
    )
    return token


async def get_session(token: str):
    pool = await get_db()
    return await pool.fetchrow(
        "SELECT *, full_name AS patient_name FROM tokens WHERE token = $1", token
    )


async def get_result(token: str):
    pool = await get_db()
    return await pool.fetchrow(
        """
        SELECT r.*, t.doctor_id, t.full_name AS patient_name
        FROM results r
        JOIN tokens t ON t.token = r.token
        WHERE r.token = $1
        ORDER BY r.completed_at DESC LIMIT 1
        """,
        token
    )


async def get_doctor_sessions(doctor_id: int, status: str = None):
    pool = await get_db()
    if status:
        return await pool.fetch(
            """
            SELECT t.token, t.full_name AS patient_name, t.test_type,
                   t.created_at, t.status, r.score, r.severity
            FROM tokens t
            LEFT JOIN results r ON r.token = t.token
            WHERE t.doctor_id = $1 AND t.status = $2
            ORDER BY t.created_at DESC LIMIT 20
            """,
            doctor_id, status
        )
    return await pool.fetch(
        """
        SELECT t.token, t.full_name AS patient_name, t.test_type,
               t.created_at, t.status, r.score, r.severity
        FROM tokens t
        LEFT JOIN results r ON r.token = t.token
        WHERE t.doctor_id = $1
        ORDER BY t.created_at DESC LIMIT 20
        """,
        doctor_id
    )


# ── QR Generation ────────────────────────────────────────────

async def generate_qr_bytes(url: str) -> bytes:
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={url}&format=png&ecc=M"
    async with aiohttp.ClientSession() as session:
        async with session.get(qr_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            return await resp.read()


# ── PDF Report ───────────────────────────────────────────────


# ── Keyboards ────────────────────────────────────────────────

def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Новий тест",         callback_data="menu:newtest")],
        [InlineKeyboardButton(text="📋 Активні сесії",      callback_data="menu:sessions")],
        [InlineKeyboardButton(text="✅ Завершені тести",    callback_data="menu:completed")],
        [InlineKeyboardButton(text="❓ Допомога",           callback_data="menu:help")],
    ])


TEST_GROUPS = [
    ("trauma",  "🧠 Травма та суїцидальний ризик", ["pcl5", "c_ssrs"]),
    ("express", "📋 Комплексний скринінг", ["express"]),
    ("mood",    "📝 Настрій і тривога",            ["phq9", "gad7", "who5"]),
    ("sleep",   "😴 Сон, стрес, соматика",         ["isi", "pss10", "phq15", "nsi"]),
    ("alcohol", "🍷 Алкоголь",                     ["audit_c", "audit"]),
    ("profile", "🔍 Особистість і копінг",         ["minmult", "schmishek", "brief_cope"]),
]
# Кожен тест із TESTS має бути рівно в одній групі, інакше бот не стартує (захист від "загубленої" кнопки)
assert sorted(k for _, _, ks in TEST_GROUPS for k in ks) == sorted(TESTS), "TEST_GROUPS не збігається з TESTS"


def kb_test_selection(group=None) -> InlineKeyboardMarkup:
    if group is None:
        buttons = [
            [InlineKeyboardButton(text=title, callback_data=f"testgrp:{gid}")]
            for gid, title, _ in TEST_GROUPS
        ]
        buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:main")])
    else:
        keys = next(ks for gid, _, ks in TEST_GROUPS if gid == group)
        buttons = [
            [InlineKeyboardButton(text=f"{TESTS[k]['emoji']} {TESTS[k]['name']}", callback_data=f"test:{k}")]
            for k in keys
        ]
        buttons.append([InlineKeyboardButton(text="◀️ До груп", callback_data="testgrp:root")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_sex() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👨 Чоловіча", callback_data="sex:M"),
            InlineKeyboardButton(text="👩 Жіноча",   callback_data="sex:F"),
        ],
        [InlineKeyboardButton(text="❌ Скасувати", callback_data="sex:cancel")],
    ])


def kb_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Підтвердити",   callback_data="confirm:yes"),
            InlineKeyboardButton(text="✏️ Змінити ПІБ",  callback_data="confirm:rename"),
        ],
        [InlineKeyboardButton(text="❌ Скасувати", callback_data="confirm:cancel")],
    ])


def kb_session_item(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Переглянути результат", callback_data=f"view:{token}")],
        [InlineKeyboardButton(text="📥 Завантажити PDF",       callback_data=f"pdf:{token}")],
        [InlineKeyboardButton(text="◀️ До списку",             callback_data="menu:completed")],
    ])


# ── Handlers ─────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    await ensure_doctor(msg.from_user.id, msg.from_user.full_name)
    await msg.answer(
        f"👋 Вітаю, <b>{_h(msg.from_user.full_name)}</b>!\n\nОберіть дію в меню нижче:",
        reply_markup=kb_main_menu(), parse_mode="HTML"
    )


@router.callback_query(F.data == "menu:main")
async def cb_main_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("📋 <b>Головне меню</b>\n\nОберіть дію:",
                               reply_markup=kb_main_menu(), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "menu:newtest")
@router.message(Command("newtest"))
async def cmd_newtest(event, state: FSMContext):
    await state.set_state(NewTest.choosing_test)
    text = "📋 <b>Оберіть тест для пацієнта:</b>"
    markup = kb_test_selection()
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("testgrp:"), NewTest.choosing_test)
async def cb_test_group(cb: CallbackQuery, state: FSMContext):
    gid = cb.data.split(":", 1)[1]
    if gid == "root":
        await cb.message.edit_text("📋 <b>Оберіть тест для пацієнта:</b>",
                                   reply_markup=kb_test_selection(), parse_mode="HTML")
    else:
        title = next((t for g, t, _ in TEST_GROUPS if g == gid), None)
        if title is None:
            await cb.answer("Невідома група", show_alert=True)
            return
        await cb.message.edit_text(f"📋 <b>{title}</b>\nОберіть тест:",
                                   reply_markup=kb_test_selection(gid), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data.startswith("test:"), NewTest.choosing_test)
async def cb_test_selected(cb: CallbackQuery, state: FSMContext):
    test_type = cb.data.split(":")[1]
    if test_type not in TESTS:
        await cb.answer("Невідомий тест", show_alert=True)
        return
    await state.update_data(test_type=test_type, sex=None)
    await state.set_state(NewTest.entering_name)
    await cb.message.edit_text(
        f"✅ Обрано: <b>{_h(TESTS[test_type]['name'])}</b>\n\n"
        "👤 Введіть <b>ПІБ пацієнта</b>:",
        parse_mode="HTML"
    )
    await cb.answer()


import html as _htmlmod


def _h(v) -> str:
    return _htmlmod.escape(str(v if v is not None else '—'), quote=False)


async def _owns(cb, result) -> bool:
    pool = await get_db()
    row = await pool.fetchrow("SELECT telegram_id FROM doctors WHERE id = $1", result['doctor_id'])
    return bool(row) and row['telegram_id'] == cb.from_user.id


@router.message(NewTest.entering_name)
async def handle_patient_name(msg: Message, state: FSMContext):
    name = (msg.text or '').strip()
    if len(name.split()) < 2 or len(name) > 120:
        await msg.answer("⚠️ Вкажіть щонайменше ім'я та прізвище (до 120 символів). Спробуйте ще раз:")
        return
    data = await state.get_data()
    await state.update_data(patient_name=name)
    if data['test_type'] in ('minmult', 'audit_c', 'phq15', 'express'):  # SEX_TESTS
        await state.set_state(NewTest.choosing_sex)
        await msg.answer(
            f"👤 Пацієнт: <b>{_h(name)}</b>\n\n"
            "⚧ Оберіть <b>стать пацієнта</b> (потрібна для норм і коректного підрахунку):",
            reply_markup=kb_sex(), parse_mode="HTML"
        )
        return
    await state.set_state(NewTest.confirming)
    await msg.answer(
        f"📝 <b>Перевірте дані:</b>\n\n"
        f"👤 Пацієнт: <b>{_h(name)}</b>\n"
        f"🧪 Тест: <b>{_h(TESTS[data['test_type']]['name'])}</b>\n\n"
        "Все вірно?",
        reply_markup=kb_confirm(), parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("sex:"), NewTest.choosing_sex)
async def cb_sex_selected(cb: CallbackQuery, state: FSMContext):
    val = cb.data.split(":")[1]
    if val == "cancel":
        await state.clear()
        await cb.message.edit_text("❌ Скасовано.", reply_markup=kb_main_menu())
        await cb.answer()
        return
    if val not in ("M", "F"):
        await cb.answer("Невідомий вибір", show_alert=True)
        return
    await state.update_data(sex=val)
    data = await state.get_data()
    await state.set_state(NewTest.confirming)
    await cb.message.edit_text(
        f"📝 <b>Перевірте дані:</b>\n\n"
        f"👤 Пацієнт: <b>{_h(data['patient_name'])}</b>\n"
        f"⚧ Стать: <b>{'Чоловіча' if val == 'M' else 'Жіноча'}</b>\n"
        f"🧪 Тест: <b>{_h(TESTS[data['test_type']]['name'])}</b>\n\n"
        "Все вірно?",
        reply_markup=kb_confirm(), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("confirm:"), NewTest.confirming)
async def cb_confirm(cb: CallbackQuery, state: FSMContext):
    action = cb.data.split(":")[1]
    if action == "cancel":
        await state.clear()
        await cb.message.edit_text("❌ Скасовано.", reply_markup=kb_main_menu())
        await cb.answer()
        return
    if action == "rename":
        await state.set_state(NewTest.entering_name)
        data = await state.get_data()
        await cb.message.edit_text(
            f"✅ Обрано: <b>{_h(TESTS[data['test_type']]['name'])}</b>\n\n"
            "👤 Введіть ПІБ пацієнта ще раз:",
            parse_mode="HTML"
        )
        await cb.answer()
        return

    await cb.message.edit_text("⏳ Створюю QR-код...")
    await cb.answer()
    data = await state.get_data()

    try:
        doctor_id = await ensure_doctor(cb.from_user.id, cb.from_user.full_name)
        token     = await create_session(doctor_id, data["patient_name"], data["test_type"], data.get("sex"))
        test_url  = f"{MINI_APP_URL}?token={token}"
        qr_bytes  = await generate_qr_bytes(test_url)
        qr_file   = BufferedInputFile(qr_bytes, filename=f"test_{token[:8]}.png")

        await cb.message.answer_photo(
            photo=qr_file,
            caption=(
                f"✅ <b>QR-код готовий!</b>\n\n"
                f"👤 Пацієнт: <b>{_h(data['patient_name'])}</b>\n"
                f"🧪 Тест: <b>{_h(TESTS[data['test_type']]['name'])}</b>\n\n"
                f"📱 Надішліть посилання або покажіть QR:\n"
                f"<code>{_h(test_url)}</code>\n\n"
                f"⚠️ <i>Одноразове посилання: діє до завершення тесту</i>"
            ),
            parse_mode="HTML"
        )
        await cb.message.delete()
    except Exception as e:
        log.error(f"Session creation error: {e}", exc_info=True)
        await cb.message.edit_text("❌ Сталася помилка. Спробуйте ще раз.", reply_markup=kb_main_menu())
        if ADMIN_TG_ID:
            await bot.send_message(ADMIN_TG_ID, f"🚨 /newtest error\nDoctor: {cb.from_user.id}\n{e}")
    finally:
        await state.clear()


_cssrs_issued = set()


@router.callback_query(F.data.startswith("cssrs:"))
async def cb_assign_cssrs(cb: CallbackQuery):
    token = cb.data.split(":", 1)[1]
    pool = await get_db()
    row = await pool.fetchrow("SELECT doctor_id, full_name FROM tokens WHERE token = $1", token)
    if not row:
        await cb.answer("Сесію не знайдено", show_alert=True)
        return
    doc = await pool.fetchrow("SELECT telegram_id FROM doctors WHERE id = $1", row['doctor_id'])
    if not doc or doc['telegram_id'] != cb.from_user.id:
        await cb.answer("Немає доступу", show_alert=True)
        return
    if token in _cssrs_issued:
        await cb.answer("C-SSRS для цього тесту вже створено", show_alert=True)
        return
    _cssrs_issued.add(token)
    await cb.answer()
    try:
        new_token = await create_session(row['doctor_id'], row['full_name'], 'c_ssrs', None)
        test_url = f"{MINI_APP_URL}?token={new_token}"
        qr_bytes = await generate_qr_bytes(test_url)
        await cb.message.answer_photo(
            photo=BufferedInputFile(qr_bytes, filename=f"cssrs_{new_token[:8]}.png"),
            caption=(
                f"🆘 <b>C-SSRS призначено</b>\n\n"
                f"👤 Пацієнт: <b>{_h(row['full_name'])}</b>\n\n"
                f"📱 Надішліть посилання або покажіть QR:\n"
                f"<code>{_h(test_url)}</code>\n\n"
                f"⚠️ <i>Одноразове посилання: діє до завершення тесту</i>"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        _cssrs_issued.discard(token)
        log.error(f"C-SSRS session error: {e}", exc_info=True)
        await cb.message.answer("❌ Не вдалося створити сесію C-SSRS. Спробуйте ще раз.")


@router.callback_query(F.data == "menu:sessions")
@router.message(Command("sessions"))
async def cmd_sessions(event, state: FSMContext):
    await state.clear()
    user_id = event.from_user.id
    pool = await get_db()
    doctor = await pool.fetchrow("SELECT id FROM doctors WHERE telegram_id = $1", user_id)
    if not doctor:
        text = "❌ Помилка: лікаря не знайдено"
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(text)
            await event.answer()
        else:
            await event.answer(text)
        return

    rows = await get_doctor_sessions(doctor['id'], status='pending')
    if not rows:
        text = "📭 Активних сесій немає.\n\nСтворіть новий тест у меню."
    else:
        text = "📋 <b>Активні сесії (тест ще не завершено):</b>\n\n"
        for r in rows:
            test_name = TESTS.get(r["test_type"], {}).get("name", r["test_type"])
            created   = r["created_at"].strftime("%d.%m %H:%M")
            text += f"• <b>{_h(r['patient_name'])}</b> — {_h(test_name)}\n"
            text += f"  <code>{_h(str(r['token'])[:8])}...</code> · {created}\n\n"
    markup = kb_main_menu()

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data == "menu:completed")
async def cmd_completed(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    pool = await get_db()
    doctor = await pool.fetchrow("SELECT id FROM doctors WHERE telegram_id = $1", cb.from_user.id)
    if not doctor:
        await cb.message.edit_text("❌ Помилка: лікаря не знайдено")
        await cb.answer()
        return

    rows = await get_doctor_sessions(doctor['id'], status='completed')
    if not rows:
        await cb.message.edit_text("📭 Завершених тестів ще немає.", reply_markup=kb_main_menu())
        await cb.answer()
        return

    buttons = [
        [InlineKeyboardButton(
            text=f"{r['patient_name']} — {TESTS.get(r['test_type'], {}).get('name', r['test_type'])} ({r['created_at'].strftime('%d.%m')})",
            callback_data=f"view:{r['token']}"
        )]
        for r in rows
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:main")])
    await cb.message.edit_text("✅ <b>Завершені тести:</b>\n",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                               parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data.startswith("view:"))
async def cb_view_result(cb: CallbackQuery):
    token  = cb.data.split(":")[1]
    result = await get_result(token)
    if not result:
        await cb.answer("❌ Результат не знайдено", show_alert=True)
        return
    if not await _owns(cb, result):
        await cb.answer("⛔ Немає доступу", show_alert=True)
        return

    import html as _html
    esc = lambda v: _html.escape(str(v if v is not None else '—'), quote=False)
    yn = lambda b: 'так' if b else 'ні'
    r = dict(result)
    d = parse_details(r.get('details'))
    cl = d.get('clusters')
    test_name = TESTS.get(r['test_type'], {}).get('name', r['test_type'])
    lines = ["📊 <b>Результат тесту</b>", "",
             f"👤 Пацієнт: <b>{esc(r['patient_name'])}</b>",
             f"🧪 Тест: {esc(test_name)}",
             f"📅 Дата: {r['completed_at'].strftime('%d.%m.%Y %H:%M')}"] + ([f"⚧ Стать: <b>{'Чоловіча' if (d.get('sex') or d.get('sex_used')) == 'M' else 'Жіноча'}</b>"] if r['test_type'] != 'minmult' and (d.get('sex') or d.get('sex_used')) in ('M', 'F') else []) + ([f"⚧ Стать: <b>{'Чоловіча' if d.get('sex') == 'M' else 'Жіноча'}</b>"] if r['test_type'] == 'minmult' and d.get('sex') in ('M', 'F') else []) + [""]
    if r['test_type'] == 'pcl5' and cl:
        lines += [f"📈 Сума: <b>{esc(r['score'])}</b> з 80",
                  f"B: {cl['B']}/20 · C: {cl['C']}/8 · D: {cl['D']}/28 · E: {cl['E']}/24",
                  f"Поріг 31–33: {yn(d.get('cutoff_met'))}; DSM-5 (попередньо): {yn(d.get('dsm_provisional'))}"]
    elif r['test_type'] == 'schmishek' and d.get('profile'):
        lvl = {'accentuation': 'акцентуація', 'tendency': 'тенденція', 'normal': 'норма'}
        lines += ["📈 <b>Профіль акцентуацій (бал з 24):</b>"]
        lines += [f"{esc(p['name'])}: <b>{esc(p['score'])}</b> — {lvl.get(p['level'], '')}" for p in d['profile']]
        lines += ["", "Акцентуація: понад 19; тенденція: 15–19. Інтерпретує фахівець."]
    elif r['test_type'] in SCREEN_MAX:
        i9, alert = item9_alert(r, d)
        if alert:
            lines += [f"<b>{'🚨' if i9 == 'yes' else '⚠️'} {esc(alert)}</b>", ""]
        lines += [f"📈 Бал: <b>{esc(r['score'])}</b> з {SCREEN_MAX[r['test_type']]}",
                  f"🎯 Рівень: <b>{esc(severity_ua(r['test_type'], r.get('severity'), r.get('score')))}</b>"]
        if r['test_type'] == 'gad7':
            lines += [f"Поріг ≥10: {gad7_cutoff(r.get('score'))}"]
        lines += ["", "Скринінг, не діагноз. Інтерпретує фахівець."]
    elif r['test_type'] == 'express' and d.get('scales'):
        lines += ["📋 <b>Комплексний скринінг: зведення по шкалах</b>", ""]
        lines += [f"{esc(x.get('name'))}: <b>{esc(x.get('score'))}</b>/{esc(x.get('max'))} — {esc(x.get('level_ua'))}" for x in d['scales']]
        if d.get('item9_positive'):
            lines += ["", "🚨 Позитивна відповідь на пункт 9 PHQ-9 — потрібна пріоритетна оцінка суїцидального ризику."]
        lines += ["", "Загального балу немає. Скринінг, не діагноз. Інтерпретує фахівець."]
    elif r['test_type'] == 'c_ssrs':
        lines += [f"🆘 <b>Суїцидальний ризик: {esc(severity_ua(r['test_type'], r.get('severity'), r.get('score')))}</b>", ""]
        _yes = d.get('yes_items') or []
        lines += [f"Пункти з відповіддю «Так»: {esc(', '.join(str(x) for x in _yes) if _yes else 'немає')}"]
        if d.get('q6_recent'):
            lines += ["⚠️ Поведінка за останні 3 місяці (п. 6)."]
        lines += ["", "Скринінг, не діагноз. Оцінку ризику та подальші дії визначає фахівець."]
    elif r['test_type'] == 'brief_cope' and d.get('subscales'):
        sub = d['subscales']
        lines += ["📈 <b>Профіль копінгу (підшкали, 0–6):</b>"]
        lines += [f"{esc(str(k).replace('_', ' '))}: <b>{esc(v)}</b>"
                  for k, v in sorted(sub.items(), key=lambda kv: -kv[1])]
        mac = d.get('macro')
        if mac:
            lines += ["", "Умовне групування (середнє 0–3):"]
            lines += [f"{esc(k)}: <b>{esc(m.get('mean'))}</b>" for k, m in mac.items()]
        if d.get('substance_flag'):
            lines += ["", "⚠️ Підшкала «Вживання речовин» ≥ 3 з 6 — зверніть увагу."]
        lines += ["", "Порогів немає. Інтерпретує фахівець."]
    elif r['test_type'] == 'minmult' and d.get('subscales'):
        v = d.get('validity') or {}
        ts = d.get('t_scores') or {}
        tv = d.get('t_validity') or {}
        mm = lambda n: ('{} ({}) {}'.format(*n.split('_', 2)) if n.count('_') >= 2 else n)
        order = lambda kv: int(kv[0].split('_')[1]) if kv[0].count('_') >= 2 else 99
        rows = sorted(d['subscales'].items(), key=order)
        if ts:
            lines += ["📈 <b>Шкали: бал (з поправкою на K) · T-бал</b>"]
            lines += [f"{esc(mm(n))}: <b>{esc(x)}</b> · T={esc(ts.get(n))}" for n, x in rows]
            lines += ["", f"L: {esc(v.get('L'))} (T={esc(tv.get('L'))}) · F: {esc(v.get('F'))} (T={esc(tv.get('F'))}) · K: {esc(v.get('K'))} (T={esc(tv.get('K'))})",
                      "T = 50 + 10·(X − M)/σ, норми за статтю. Інтерпретує фахівець."]
        else:
            lines += ["📈 <b>Шкали (з поправкою на K):</b>"]
            lines += [f"{esc(mm(n))}: <b>{esc(x)}</b>" for n, x in rows]
            lines += ["", f"L: {esc(v.get('L'))} · F: {esc(v.get('F'))} · K: {esc(v.get('K'))}",
                      "Т-бали не розраховано (стать не вказана); інтерпретує фахівець."]
    else:
        lines += [f"📈 Бал: <b>{esc(r['score'])}</b>", "Інтерпретація потребує фахівця."]
    text = "\n".join(lines)
    markup = kb_session_item(token)
    if r['test_type'] in TESTS:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 По пунктах", callback_data=f"items:{token}")]
        ] + list(markup.inline_keyboard))
    await cb.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await cb.answer()


def _paginate_lines(lines, limit=3000):
    pages, cur, n = [], [], 0
    for x in lines:
        if cur and n + len(x) + 1 > limit:
            pages.append(cur)
            cur, n = [], 0
        cur.append(x)
        n += len(x) + 1
    if cur:
        pages.append(cur)
    return pages


@router.callback_query(F.data.startswith("items:"))
async def cb_view_items(cb: CallbackQuery):
    parts = cb.data.split(":")
    token = parts[1]
    try:
        page = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        page = 0
    result = await get_result(token)
    if not result:
        await cb.answer("❌ Результат не знайдено", show_alert=True)
        return
    if not await _owns(cb, result):
        await cb.answer("⛔ Немає доступу", show_alert=True)
        return
    import html as _html
    r = dict(result)
    d = parse_details(r.get('details'))
    rows = item_lines(r['test_type'], r, d)
    pages = _paginate_lines(rows)
    page = max(0, min(page, len(pages) - 1)) if pages else 0
    test_name = TESTS.get(r['test_type'], {}).get('name', r['test_type'])
    pg = f" (стор. {page + 1}/{len(pages)})" if len(pages) > 1 else ""
    head = f"📋 <b>Відповіді по пунктах</b>{pg}\n🧪 {_html.escape(str(test_name), quote=False)}\n"
    if pages:
        body = "\n".join(_html.escape(x, quote=False) for x in pages[page])
        scored = r['test_type'] in ('pcl5', 'phq9', 'gad7', 'isi', 'phq15', 'pss10', 'who5', 'nsi', 'audit_c', 'audit') and r.get('score') is not None
        foot = f"\n\n📈 Бал: <b>{_html.escape(str(r['score']), quote=False)}</b>" if scored else ""
    else:
        body = "Відповіді по пунктах не збережено або недоступні."
        foot = ""
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"items:{token}:{page - 1}"))
    if page < len(pages) - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"items:{token}:{page + 1}"))
    kb = ([nav] if nav else []) + [[InlineKeyboardButton(text="◀️ До результату", callback_data=f"view:{token}")]]
    await cb.message.edit_text(head + "\n" + body + foot,
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data.startswith("pdf:"))
async def cb_download_pdf(cb: CallbackQuery):
    await cb.answer("📄 Формую PDF...")
    token  = cb.data.split(":")[1]
    result = await get_result(token)
    if not result:
        await cb.answer("❌ Результат не знайдено", show_alert=True)
        return
    if not await _owns(cb, result):
        await cb.answer("⛔ Немає доступу", show_alert=True)
        return
    try:
        pdf_buffer = generate_pdf_report({**dict(result), 'test_name': TESTS.get(result['test_type'], {}).get('name', result['test_type'])})
        test_name   = TESTS.get(result['test_type'], {}).get('name', result['test_type']).replace(' ', '_')
        patient_name = result['patient_name'].replace(' ', '_')
        filename    = f"Report_{patient_name}_{test_name}_{datetime.now().strftime('%d%m%Y')}.pdf"
        await bot.send_document(
            cb.from_user.id,
            document=BufferedInputFile(pdf_buffer.read(), filename=filename),
            caption=f"📄 Звіт: {result['patient_name']} — {test_name}"
        )
    except Exception as e:
        log.error(f"PDF error: {e}", exc_info=True)
        await cb.answer("❌ Не вдалося сформувати PDF", show_alert=True)


@router.callback_query(F.data == "menu:help")
@router.message(Command("help"))
async def cmd_help(event, state: FSMContext):
    await state.clear()
    text = (
        "📖 <b>Допомога</b>\n\n"
        "<b>Команди:</b>\n"
        "• /start — головне меню\n"
        "• /newtest — створити тест\n"
        "• /sessions — активні сесії\n"
        "• /help — ця довідка\n\n"
        "<b>Як це працює:</b>\n"
        "1️⃣ Створіть тест для пацієнта\n"
        "2️⃣ Оберіть методику\n"
        "3️⃣ Введіть ПІБ пацієнта\n"
        "4️⃣ Отримайте QR-код або посилання\n"
        "5️⃣ Пацієнт проходить тест\n"
        "6️⃣ Результат надходить автоматично\n"
        "7️⃣ Перегляньте деталі та завантажте PDF"
    )
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb_main_menu(), parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb_main_menu(), parse_mode="HTML")


# ── Webhook (receives results from n8n) ──────────────────────

async def handle_result_webhook(request):
    import html as _html

    def esc(value, limit=200):
        # Everything shown in the doctor's message is escaped for parse_mode="HTML".
        text = str(value if value is not None else '—')[:limit]
        return _html.escape(text, quote=False)

    try:
        try:
            data = await request.json()
        except ValueError:
            return web.json_response({'error': 'Invalid JSON'}, status=400)
        if not isinstance(data, dict):
            return web.json_response({'error': 'Invalid body'}, status=400)

        token = data.get('session_token')
        if not isinstance(token, str) or not token:
            return web.json_response({'error': 'Missing session_token'}, status=400)

        session = await get_session(token)
        if not session:
            return web.json_response({'error': 'Session not found'}, status=404)

        pool = await get_db()
        doctor = await pool.fetchrow("SELECT telegram_id FROM doctors WHERE id = $1", session['doctor_id'])
        if not doctor:
            return web.json_response({'error': 'Doctor not found'}, status=404)

        test_type    = str(data.get('test_type', ''))[:50]
        test_name    = TESTS.get(test_type, {}).get('name', test_type)
        # The patient name comes from the server-side session, not from the request body.
        patient_name = session['full_name'] or '—'
        score        = data.get('score', '—')
        severity     = data.get('severity_ua') or severity_ua(test_type, data.get('severity'), data.get('score'))
        i9, alert    = item9_alert({**data, 'test_type': test_type}, data.get('details') if isinstance(data.get('details'), dict) else {})
        _cs = data.get('severity') if test_type == 'c_ssrs' else None
        sev_emoji    = '🔴' if (i9 == 'yes' or _cs == 'high') else ('🟠' if (data.get('ptsd_probable') or _cs == 'moderate') else '⚪')

        if test_type == 'minmult':
            score_block = "📋 Профіль шкал — у результаті (кнопка нижче).\n\n"
        elif test_type == 'schmishek':
            acc = data.get('accentuated_types') or []
            tend = (data.get('details') or {}).get('tendency_types') or []
            score_block = (
                f"🎯 Акцентуації (понад 19): <b>{esc(', '.join(acc) if acc else 'не виявлено', 300)}</b>\n"
                + (f"〰️ Тенденція (15–19): {esc(', '.join(tend), 300)}\n" if tend else "")
                + "\n"
            )
        elif test_type == 'express':
            _dt = data.get('details') if isinstance(data.get('details'), dict) else {}
            _sc = _dt.get('scales') or []
            score_block = (
                "📋 <b>Зведення по шкалах:</b>\n"
                + "".join(f"• {esc(x.get('name'), 40)}: <b>{esc(x.get('score'), 10)}</b>/{esc(x.get('max'), 10)} — {esc(x.get('level_ua'), 80)}\n" for x in _sc)
                + "\nПовний звіт — у результаті (кнопка нижче).\n\n"
            )
        elif test_type == 'c_ssrs':
            _lvl = data.get('severity')
            _icon = {'high': '🚨', 'moderate': '⚠️', 'low': '🟡', 'none': '⚪'}.get(_lvl, '⚪')
            score_block = (
                f"{_icon} <b>Суїцидальний ризик: {esc(severity, 300)}</b>\n"
                + ("Потрібна негайна оцінка фахівцем.\n" if _lvl in ('high', 'moderate') else "")
                + "\n"
            )
        elif test_type == 'brief_cope':
            _dt = data.get('details') if isinstance(data.get('details'), dict) else {}
            _top = _dt.get('top3') or []
            score_block = (
                f"🛡️ Найвиразніші стратегії: <b>{esc(', '.join(_top) if _top else '—', 300)}</b>\n"
                + ("⚠️ Вживання речовин ≥ 3 з 6\n" if _dt.get('substance_flag') else "")
                + "Повний профіль — у результаті (кнопка нижче).\n\n"
            )
        else:
            score_block = f"📈 Бал: <b>{esc(score, 20)}</b>\n🎯 Підсумок: <b>{esc(severity, 300)}</b>\n\n"
        message = (
            (f"<b>{'🚨' if i9 == 'yes' else '⚠️'} {esc(alert, 300)}</b>\n\n" if alert else "")
            + f"{sev_emoji} <b>Тест завершено</b>\n\n"
            f"👤 Пацієнт: <b>{esc(patient_name)}</b>\n"
            f"🧪 Тест: {esc(test_name)}\n"
            f"{score_block}"
            f"Кнопками нижче можна переглянути результат або завантажити PDF."
        )
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Переглянути результат",   callback_data=f"view:{token}")],
            [InlineKeyboardButton(text="📥 Завантажити PDF",  callback_data=f"pdf:{token}")],
        ])
        if test_type in ('phq9', 'express') and (i9 == 'yes' or data.get('item9_positive')):
            markup = InlineKeyboardMarkup(inline_keyboard=markup.inline_keyboard + [
                [InlineKeyboardButton(text="🆘 Призначити C-SSRS", callback_data=f"cssrs:{token}")]])
        await bot.send_message(doctor['telegram_id'], message, parse_mode="HTML", reply_markup=markup)
        log.info(f"Result notification sent to doctor {doctor['telegram_id']} for token {token}")
        return web.json_response({'status': 'ok'})

    except Exception as e:
        log.error(f"Webhook error: {e}", exc_info=True)
        return web.json_response({'error': 'internal error'}, status=500)


# -- Startup / Shutdown --

async def setup_bot_menu():
    commands = [
        BotCommand(command="start",    description="🏠 Головне меню"),
        BotCommand(command="newtest",  description="➕ Створити тест"),
        BotCommand(command="sessions", description="📋 Активні сесії"),
        BotCommand(command="help",     description="❓ Допомога"),
    ]
    await bot.set_my_commands(commands)
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


@web.middleware
async def require_secret(request, handler):
    import hmac
    got = request.headers.get("X-Webhook-Secret", "")
    if not hmac.compare_digest(got.encode(), WEBHOOK_SECRET.encode()):
        log.warning(f"Rejected /result from {request.remote}")
        return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


async def main():
    if not WEBHOOK_SECRET:
        raise SystemExit("WEBHOOK_SECRET is required")
    app = web.Application(middlewares=[require_secret])
    app.router.add_post(WEBHOOK_PATH, handle_result_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, WEBHOOK_HOST, WEBHOOK_PORT).start()

    await get_db()
    await setup_bot_menu()
    log.info(f"Webhook server started on port {WEBHOOK_PORT}")

    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        if db_pool:
            await db_pool.close()
        await bot.session.close()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped")
