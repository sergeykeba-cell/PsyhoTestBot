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
WEBHOOK_PATH = "/result"
ADMIN_TG_ID  = int(os.getenv("ADMIN_TG_ID", "0"))

TESTS = {
    "pcl5":      {"name": "PCL-5 (PTSD)",                    "emoji": "🧠"},
    "minmult":   {"name": "Mini-Mult (short MMPI)",           "emoji": "📋"},
    "schmishek": {"name": "Schmishek (character accentuations)", "emoji": "🔍"},
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


async def create_session(doctor_id: int, patient_name: str, test_type: str) -> str:
    pool = await get_db()
    token = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO tokens (token, doctor_id, full_name, test_type, status) VALUES ($1, $2, $3, $4, 'pending')",
        token, doctor_id, patient_name, test_type
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

def generate_pdf_report(result_data: dict) -> BytesIO:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    story = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('Title', parent=styles['Heading1'],
                                 fontSize=18, spaceAfter=30, alignment=1)
    heading_style = ParagraphStyle('Heading', parent=styles['Heading2'],
                                   fontSize=14, textColor=colors.HexColor('#2563eb'),
                                   spaceAfter=12, spaceBefore=12)
    normal_style = ParagraphStyle('Normal', parent=styles['Normal'],
                                  fontSize=11, leading=14)

    test_name = TESTS.get(result_data.get('test_type', ''), {}).get('name', result_data.get('test_type', ''))
    story.append(Paragraph("TEST REPORT", title_style))
    story.append(Paragraph(test_name, heading_style))
    story.append(Spacer(1, 0.5*cm))

    completed_at = result_data.get('completed_at', datetime.now())
    if isinstance(completed_at, str):
        completed_at = datetime.now()

    info_data = [
        ['Patient:', result_data.get('patient_name', '—')],
        ['Date:', completed_at.strftime('%d.%m.%Y %H:%M')],
        ['Total score:', str(result_data.get('score', '—'))],
        ['Severity level:', str(result_data.get('severity', '—')).upper()],
    ]
    info_table = Table(info_data, colWidths=[5*cm, 10*cm])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f4f4f5')),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 1*cm))

    story.append(Paragraph("INTERPRETATION", heading_style))
    severity  = result_data.get('severity', 'low')
    score     = result_data.get('score', 0)
    test_type = result_data.get('test_type', '')

    interp_map = {
        'pcl5': {
            'low':      f"Total score {score} indicates minimal PTSD symptoms. Result is within normal range.",
            'moderate': f"Total score {score} indicates mild PTSD symptoms. Monitoring recommended.",
            'high':     f"Total score {score} indicates moderate PTSD symptoms. Specialist consultation recommended.",
            'severe':   f"Total score {score} indicates severe PTSD symptoms. Psychotherapeutic intervention required.",
        },
        'minmult': {
            'low':      "Results within normal range. No significant personality deviations detected.",
            'moderate': "Some personality accentuations detected (sub-norm). Clarifying diagnostics recommended.",
            'high':     "Personality profile deviations detected. Psychological consultation recommended.",
            'severe':   "Significant personality profile deviations. Psychological assistance recommended.",
        },
        'schmishek': {
            'low':      "No character accentuations detected. Personality profile within normal range.",
            'moderate': "Character accentuations detected (normal variant). Consider in communication approach.",
            'high':     "Pronounced character accentuations detected. Psychologist consultation recommended.",
            'severe':   "Very pronounced character accentuations. Psychological assistance required.",
        },
    }
    interp = interp_map.get(test_type, {}).get(severity, f"Score: {score}, Severity: {severity}")
    story.append(Paragraph(interp, normal_style))

    story.append(Spacer(1, 1*cm))
    story.append(Paragraph("DISCLAIMER", heading_style))
    story.append(Paragraph(
        "This report is for informational purposes only and does not constitute a medical diagnosis. "
        "For professional consultation and result interpretation, please refer to a qualified specialist.",
        normal_style
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer


# ── Keyboards ────────────────────────────────────────────────

def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ New Test",          callback_data="menu:newtest")],
        [InlineKeyboardButton(text="📋 Active Sessions",   callback_data="menu:sessions")],
        [InlineKeyboardButton(text="✅ Completed Tests",   callback_data="menu:completed")],
        [InlineKeyboardButton(text="❓ Help",              callback_data="menu:help")],
    ])


def kb_test_selection() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"{v['emoji']} {v['name']}", callback_data=f"test:{k}")]
        for k, v in TESTS.items()
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Back", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Confirm",    callback_data="confirm:yes"),
            InlineKeyboardButton(text="✏️ Edit name", callback_data="confirm:rename"),
        ],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="confirm:cancel")],
    ])


def kb_session_item(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 View result",    callback_data=f"view:{token}")],
        [InlineKeyboardButton(text="📥 Download PDF",   callback_data=f"pdf:{token}")],
        [InlineKeyboardButton(text="◀️ Back to list",   callback_data="menu:completed")],
    ])


# ── Handlers ─────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    await ensure_doctor(msg.from_user.id, msg.from_user.full_name)
    await msg.answer(
        f"👋 Welcome, *{msg.from_user.full_name}*!\n\nChoose an action from the menu below:",
        reply_markup=kb_main_menu(), parse_mode="Markdown"
    )


@router.callback_query(F.data == "menu:main")
async def cb_main_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("📋 *Main Menu*\n\nChoose an action:",
                               reply_markup=kb_main_menu(), parse_mode="Markdown")
    await cb.answer()


@router.callback_query(F.data == "menu:newtest")
@router.message(Command("newtest"))
async def cmd_newtest(event, state: FSMContext):
    await state.set_state(NewTest.choosing_test)
    text = "📋 *Select a test for the patient:*"
    markup = kb_test_selection()
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup, parse_mode="Markdown")


@router.callback_query(F.data.startswith("test:"), NewTest.choosing_test)
async def cb_test_selected(cb: CallbackQuery, state: FSMContext):
    test_type = cb.data.split(":")[1]
    if test_type not in TESTS:
        await cb.answer("Unknown test", show_alert=True)
        return
    await state.update_data(test_type=test_type)
    await state.set_state(NewTest.entering_name)
    await cb.message.edit_text(
        f"✅ Selected: *{TESTS[test_type]['name']}*\n\n"
        "👤 Enter the *patient's full name*:",
        parse_mode="Markdown"
    )
    await cb.answer()


@router.message(NewTest.entering_name)
async def handle_patient_name(msg: Message, state: FSMContext):
    name = msg.text.strip()
    if len(name.split()) < 2:
        await msg.answer("⚠️ Please enter at least first and last name. Try again:")
        return
    data = await state.get_data()
    await state.update_data(patient_name=name)
    await state.set_state(NewTest.confirming)
    await msg.answer(
        f"📝 *Confirm details:*\n\n"
        f"👤 Patient: *{name}*\n"
        f"🧪 Test: *{TESTS[data['test_type']]['name']}*\n\n"
        "Is this correct?",
        reply_markup=kb_confirm(), parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("confirm:"), NewTest.confirming)
async def cb_confirm(cb: CallbackQuery, state: FSMContext):
    action = cb.data.split(":")[1]
    if action == "cancel":
        await state.clear()
        await cb.message.edit_text("❌ Cancelled.", reply_markup=kb_main_menu())
        await cb.answer()
        return
    if action == "rename":
        await state.set_state(NewTest.entering_name)
        data = await state.get_data()
        await cb.message.edit_text(
            f"✅ Selected: *{TESTS[data['test_type']]['name']}*\n\n"
            "👤 Enter patient's full name again:",
            parse_mode="Markdown"
        )
        await cb.answer()
        return

    await cb.message.edit_text("⏳ Generating QR code...")
    await cb.answer()
    data = await state.get_data()

    try:
        doctor_id = await ensure_doctor(cb.from_user.id, cb.from_user.full_name)
        token     = await create_session(doctor_id, data["patient_name"], data["test_type"])
        test_url  = f"{MINI_APP_URL}?token={token}"
        qr_bytes  = await generate_qr_bytes(test_url)
        qr_file   = BufferedInputFile(qr_bytes, filename=f"test_{token[:8]}.png")

        await cb.message.answer_photo(
            photo=qr_file,
            caption=(
                f"✅ *QR code ready!*\n\n"
                f"👤 Patient: *{data['patient_name']}*\n"
                f"🧪 Test: *{TESTS[data['test_type']]['name']}*\n\n"
                f"📱 Share the link or show the QR:\n"
                f"`{test_url}`\n\n"
                f"⚠️ _Single-use link — expires after completion_"
            ),
            parse_mode="Markdown"
        )
        await cb.message.delete()
    except Exception as e:
        log.error(f"Session creation error: {e}", exc_info=True)
        await cb.message.edit_text("❌ Error occurred. Please try again.", reply_markup=kb_main_menu())
        if ADMIN_TG_ID:
            await bot.send_message(ADMIN_TG_ID, f"🚨 /newtest error\nDoctor: {cb.from_user.id}\n{e}")
    finally:
        await state.clear()


@router.callback_query(F.data == "menu:sessions")
@router.message(Command("sessions"))
async def cmd_sessions(event, state: FSMContext):
    await state.clear()
    user_id = event.from_user.id
    pool = await get_db()
    doctor = await pool.fetchrow("SELECT id FROM doctors WHERE telegram_id = $1", user_id)
    if not doctor:
        text = "❌ Error: doctor not found"
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(text)
            await event.answer()
        else:
            await event.answer(text)
        return

    rows = await get_doctor_sessions(doctor['id'], status='pending')
    if not rows:
        text = "📭 No active sessions.\n\nCreate a new test from the menu."
        markup = kb_main_menu()
    else:
        text = "📋 *Active sessions (test not yet completed):*\n\n"
        for r in rows:
            test_name = TESTS.get(r["test_type"], {}).get("name", r["test_type"])
            created   = r["created_at"].strftime("%d.%m %H:%M")
            text += f"• *{r['patient_name']}* — {test_name}\n"
            text += f"  `{str(r['token'])[:8]}...` · {created}\n\n"
        markup = kb_main_menu()

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup, parse_mode="Markdown")


@router.callback_query(F.data == "menu:completed")
async def cmd_completed(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    pool = await get_db()
    doctor = await pool.fetchrow("SELECT id FROM doctors WHERE telegram_id = $1", cb.from_user.id)
    if not doctor:
        await cb.message.edit_text("❌ Error: doctor not found")
        await cb.answer()
        return

    rows = await get_doctor_sessions(doctor['id'], status='completed')
    if not rows:
        await cb.message.edit_text("📭 No completed tests yet.", reply_markup=kb_main_menu())
        await cb.answer()
        return

    sev_emoji = {"low": "🟢", "moderate": "🟡", "high": "🟠", "severe": "🔴"}
    buttons = [
        [InlineKeyboardButton(
            text=f"{sev_emoji.get(r.get('severity'), '⚪')} {r['patient_name']} — {TESTS.get(r['test_type'], {}).get('name', r['test_type'])} ({r['created_at'].strftime('%d.%m')})",
            callback_data=f"view:{r['token']}"
        )]
        for r in rows
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Back", callback_data="menu:main")])
    await cb.message.edit_text("✅ *Completed tests:*\n",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                               parse_mode="Markdown")
    await cb.answer()


@router.callback_query(F.data.startswith("view:"))
async def cb_view_result(cb: CallbackQuery):
    token  = cb.data.split(":")[1]
    result = await get_result(token)
    if not result:
        await cb.answer("❌ Result not found", show_alert=True)
        return

    sev_labels = {
        'low': 'Minimal / Normal', 'moderate': 'Mild / Sub-norm',
        'high': 'Moderate / Deviation', 'severe': 'Severe / Pronounced'
    }
    test_name = TESTS.get(result['test_type'], {}).get('name', result['test_type'])
    text = (
        f"📊 *Test Result*\n\n"
        f"👤 Patient: *{result['patient_name']}*\n"
        f"🧪 Test: {test_name}\n"
        f"📅 Date: {result['completed_at'].strftime('%d.%m.%Y %H:%M')}\n\n"
        f"📈 Total score: *{result['score']}*\n"
        f"🎯 Level: *{sev_labels.get(result['severity'], result['severity'])}*"
    )
    await cb.message.edit_text(text, reply_markup=kb_session_item(token), parse_mode="Markdown")
    await cb.answer()


@router.callback_query(F.data.startswith("pdf:"))
async def cb_download_pdf(cb: CallbackQuery):
    await cb.answer("📄 Generating PDF...")
    token  = cb.data.split(":")[1]
    result = await get_result(token)
    if not result:
        await cb.answer("❌ Result not found", show_alert=True)
        return
    try:
        pdf_buffer = generate_pdf_report(dict(result))
        test_name   = TESTS.get(result['test_type'], {}).get('name', result['test_type']).replace(' ', '_')
        patient_name = result['patient_name'].replace(' ', '_')
        filename    = f"Report_{patient_name}_{test_name}_{datetime.now().strftime('%d%m%Y')}.pdf"
        await bot.send_document(
            cb.from_user.id,
            document=BufferedInputFile(pdf_buffer.read(), filename=filename),
            caption=f"📄 Report: {result['patient_name']} — {test_name}"
        )
    except Exception as e:
        log.error(f"PDF error: {e}", exc_info=True)
        await cb.answer("❌ PDF generation failed", show_alert=True)


@router.callback_query(F.data == "menu:help")
@router.message(Command("help"))
async def cmd_help(event, state: FSMContext):
    await state.clear()
    text = (
        "📖 *Help*\n\n"
        "*Commands:*\n"
        "• /start — main menu\n"
        "• /newtest — create new test\n"
        "• /sessions — active sessions\n"
        "• /help — this help\n\n"
        "*How it works:*\n"
        "1️⃣ Create a test for the patient\n"
        "2️⃣ Select the test methodology\n"
        "3️⃣ Enter the patient's full name\n"
        "4️⃣ Get a QR code or link\n"
        "5️⃣ Patient completes the test\n"
        "6️⃣ You receive the result automatically\n"
        "7️⃣ View details and download PDF reports"
    )
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb_main_menu(), parse_mode="Markdown")
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb_main_menu(), parse_mode="Markdown")


# ── Webhook (receives results from n8n) ──────────────────────

async def handle_result_webhook(request):
    try:
        data = await request.json()
        token = data.get('session_token')
        if not token:
            return web.json_response({'error': 'Missing session_token'}, status=400)

        session = await get_session(token)
        if not session:
            return web.json_response({'error': 'Session not found'}, status=404)

        pool = await get_db()
        doctor = await pool.fetchrow("SELECT telegram_id FROM doctors WHERE id = $1", session['doctor_id'])
        if not doctor:
            return web.json_response({'error': 'Doctor not found'}, status=404)

        test_name    = TESTS.get(data.get('test_type', ''), {}).get('name', data.get('test_type', ''))
        patient_name = data.get('patient_name', session['full_name'])
        score        = data.get('score', '—')
        severity     = data.get('severity_ua', data.get('severity', '—'))
        sev_emoji    = {"low": "🟢", "moderate": "🟡", "high": "🟠", "severe": "🔴"}.get(data.get('severity', ''), '⚪')

        message = (
            f"{sev_emoji} *Test completed!*\n\n"
            f"👤 Patient: *{patient_name}*\n"
            f"🧪 Test: {test_name}\n"
            f"📈 Score: *{score}*\n"
            f"🎯 Level: *{severity}*\n\n"
            f"Use the buttons below to view results or download a PDF report."
        )
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 View result",   callback_data=f"view:{token}")],
            [InlineKeyboardButton(text="📥 Download PDF",  callback_data=f"pdf:{token}")],
        ])
        await bot.send_message(doctor['telegram_id'], message, parse_mode="Markdown", reply_markup=markup)
        log.info(f"Result notification sent to doctor {doctor['telegram_id']} for token {token}")
        return web.json_response({'status': 'ok'})

    except Exception as e:
        log.error(f"Webhook error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


# ── Startup / Shutdown ───────────────────────────────────────

async def setup_bot_menu():
    commands = [
        BotCommand(command="start",    description="🏠 Main menu"),
        BotCommand(command="newtest",  description="➕ Create new test"),
        BotCommand(command="sessions", description="📋 Active sessions"),
        BotCommand(command="help",     description="❓ Help"),
    ]
    await bot.set_my_commands(commands)
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def main():
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, handle_result_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', WEBHOOK_PORT).start()

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
