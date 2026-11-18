import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# Токен від @BotFather задається через змінну середовища BOT_TOKEN
TOKEN = os.environ["BOT_TOKEN"]

logging.basicConfig(level=logging.INFO)
router = Router()
bot = Bot(token=TOKEN)
9033504163:AAFAYrGLgkS0G3YfS-pTkWZOYs7tYBpD-6w
dp = Dispatcher()


# --- РОБОТА З БАЗОЮ ДАНИХ SQLITE ---
def init_db():
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            role TEXT
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            manager_id INTEGER,
            worker_id INTEGER,
            status TEXT,
            completed_at TEXT
        )
    """)
  conn.commit()
  conn.close()


def set_user_role(user_id: int, full_name: str, role: str):
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        INSERT INTO users (user_id, full_name, role) 
        VALUES (?, ?, ?) 
        ON CONFLICT(user_id) DO UPDATE SET role=excluded.role, full_name=excluded.full_name
    """,
      (user_id, full_name, role),
  )
  conn.commit()
  conn.close()


def get_user_role(user_id: int):
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
  row = cursor.fetchone()
  conn.close()
  return row[0] if row else None


def get_all_workers():
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute("SELECT user_id FROM users WHERE role = 'worker'")
  rows = cursor.fetchall()
  conn.close()
  return [row[0] for row in rows]


def create_order(text: str, manager_id: int) -> int:
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        INSERT INTO orders (text, manager_id, worker_id, status, completed_at) 
        VALUES (?, ?, NULL, 'Нове', NULL)
    """,
      (text, manager_id),
  )
  order_id = cursor.lastrowid
  conn.commit()
  conn.close()
  return order_id


def get_order(order_id: int):
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      "SELECT order_id, text, manager_id, worker_id, status FROM orders WHERE"
      " order_id = ?",
      (order_id,),
  )
  row = cursor.fetchone()
  conn.close()
  if row:
    return {
        "order_id": row[0],
        "text": row[1],
        "manager_id": row[2],
        "worker_id": row[3],
        "status": row[4],
    }
  return None


def assign_worker(order_id: int, worker_id: int) -> bool:
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        UPDATE orders 
        SET worker_id = ?, status = 'Збирається' 
        WHERE order_id = ? AND worker_id IS NULL
    """,
      (worker_id, order_id),
  )
  updated = cursor.rowcount > 0
  conn.commit()
  conn.close()
  return updated


def update_order_status(order_id: int, status: str):
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  completed_time = (
      datetime.now().strftime("%Y-%m-%d %H:%M:%S")
      if status == "Зібране"
      else None
  )
  cursor.execute(
      """
        UPDATE orders 
        SET status = ?, completed_at = ? 
        WHERE order_id = ?
    """,
      (status, completed_time, order_id),
  )
  conn.commit()
  conn.close()


def get_worker_active_orders(worker_id: int):
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        SELECT order_id, text, status 
        FROM orders 
        WHERE worker_id = ? AND status != 'Зібране'
    """,
      (worker_id,),
  )
  rows = cursor.fetchall()
  conn.close()
  return rows


def get_warehouse_stats():
  """Отримує статистику: скільки зібрано замовлень кожним працівником."""
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute("""
        SELECT u.full_name, COUNT(o.order_id) as total_done
        FROM users u
        LEFT JOIN orders o ON u.user_id = o.worker_id AND o.status = 'Зібране'
        WHERE u.role = 'worker'
        GROUP BY u.user_id
        ORDER BY total_done DESC
    """)
  rows = cursor.fetchall()
  conn.close()
  return rows


# --- КЛАВІАТУРИ ---
def get_manager_menu():
  return ReplyKeyboardMarkup(
      keyboard=[
          [KeyboardButton(text="➕ Нове замовлення")],
          [KeyboardButton(text="📊 Статистика складу")],
          [KeyboardButton(text="🔄 Змінити роль")],
      ],
      resize_keyboard=True,
  )


def get_worker_menu():
  return ReplyKeyboardMarkup(
      keyboard=[
          [KeyboardButton(text="📋 Мої активні замовлення")],
          [KeyboardButton(text="🔄 Змінити роль")],
      ],
      resize_keyboard=True,
  )


# --- СТАНИ ДЛЯ FSM ---
class OrderStates(StatesGroup):
  waiting_for_order_text = State()


# --- СТАРТ ТА ВИБІР РОЛІ ---
@router.message(CommandStart())
@router.message(F.text == "🔄 Змінити роль")
async def cmd_start(message: Message, state: FSMContext):
  await state.clear()
  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="👨‍💼 Я — Менеджер", callback_data="role_manager"
              ),
              InlineKeyboardButton(
                  text="📦 Я — Складський працівник", callback_data="role_worker"
              ),
          ]
      ]
  )
  await message.answer(
      "Вітаю! Оберіть вашу роль у системі:", reply_markup=keyboard
  )


@router.callback_query(F.data.startswith("role_"))
async def set_role_callback(callback: CallbackQuery):
  user_id = callback.from_user.id
  full_name = callback.from_user.full_name
  role = callback.data.split("_")[1]
  set_user_role(user_id, full_name, role)

  await callback.message.delete()
  if role == "manager":
    await callback.message.answer(
        "✅ Ви зареєстровані як **Менеджер**.", reply_markup=get_manager_menu()
    )
  else:
    await callback.message.answer(
        "✅ Ви зареєстровані як **Складський працівник**.",
        reply_markup=get_worker_menu(),
    )
  await callback.answer()


# --- СТВОРЕННЯ ЗАМОВЛЕННЯ МЕНЕДЖЕРОМ ---
@router.message(F.text == "➕ Нове замовлення")
async def start_order(message: Message, state: FSMContext):
  if get_user_role(message.from_user.id) != "manager":
    await message.answer("Ця функція доступна лише менеджерам.")
    return

  await message.answer(
      "Введіть деталі замовлення (номер, перелік товарів, коментар):"
  )
  await state.set_state(OrderStates.waiting_for_order_text)


@router.message(OrderStates.waiting_for_order_text)
async def process_order_text(message: Message, state: FSMContext):
  order_id = create_order(message.text, message.from_user.id)
  await state.clear()

  await message.answer(
      f"✅ Замовлення **№{order_id}** успішно створено!",
      reply_markup=get_manager_menu(),
  )

  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="📥 Взяти в роботу", callback_data=f"take_{order_id}"
              )
          ]
      ]
  )

  workers = get_all_workers()
  for worker_id in workers:
    try:
      await bot.send_message(
          worker_id,
          f"📦 **Нове замовлення №{order_id}**\n\n{message.text}",
          reply_markup=keyboard,
      )
    except Exception:
      pass


# --- СТАТИСТИКА СКЛАДУ ДЛЯ МЕНЕДЖЕРА ---
@router.message(F.text == "📊 Статистика складу")
async def show_statistics(message: Message):
  if get_user_role(message.from_user.id) != "manager":
    await message.answer("Ця функція доступна лише менеджерам.")
    return

  stats = get_warehouse_stats()
  if not stats:
    await message.answer("Поки що немає даних про зібрані замовлення.")
    return

  text = "📊 **Статистика збирання замовлень:**\n\n"
  for worker_name, total_done in stats:
    text += f"👤 **{worker_name}** — зібрано замовлень: `{total_done}`\n"

  await message.answer(text, parse_mode="Markdown")


# --- ПЕРЕГЛЯД АКТИВНИХ ЗАМОВЛЕНЬ ПРАЦІВНИКА ---
@router.message(F.text == "📋 Мої активні замовлення")
async def show_active_orders(message: Message):
  if get_user_role(message.from_user.id) != "worker":
    await message.answer("Ця функція доступна лише складським працівникам.")
    return

  active_orders = get_worker_active_orders(message.from_user.id)
  if not active_orders:
    await message.answer("У вас зараз немає активних замовлень у роботі.")
    return

  for order_id, text, status in active_orders:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🟡 Збирається",
                    callback_data=f"status_assembling_{order_id}",
                ),
                InlineKeyboardButton(
                    text="🟢 Зібране", callback_data=f"status_done_{order_id}"
                ),
            ]
        ]
    )
    await message.answer(
        f"📦 **Замовлення №{order_id}** | Статус: _{status}_\n\n{text}",
        reply_markup=keyboard,
    )


# --- ВЗЯТТЯ ЗАМОВЛЕННЯ В РОБОТУ ---
@router.callback_query(F.data.startswith("take_"))
async def take_order(callback: CallbackQuery):
  if get_user_role(callback.from_user.id) != "worker":
    await callback.answer(
        "Тільки складські працівники можуть брати замовлення!", show_alert=True
    )
    return

  order_id = int(callback.data.split("_")[1])
  order = get_order(order_id)

  if not order:
    await callback.answer("Замовлення не знайдено.", show_alert=True)
    return

  success = assign_worker(order_id, callback.from_user.id)
  if not success:
    await callback.answer(
        "⚠️ Це замовлення вже забрав інший працівник!", show_alert=True
    )
    await callback.message.edit_text(
        f"❌ **Замовлення №{order_id}** вже виконується іншим працівником."
    )
    return

  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="🟡 Збирається",
                  callback_data=f"status_assembling_{order_id}",
              ),
              InlineKeyboardButton(
                  text="🟢 Зібране", callback_data=f"status_done_{order_id}"
              ),
          ]
      ]
  )

  worker_name = callback.from_user.full_name
  await callback.message.edit_text(
      f"📦 **Замовлення №{order_id}** (Відповідальний:"
      f" {worker_name})\nСтатус: _Збирається_\n\n{order['text']}",
      reply_markup=keyboard,
  )

  try:
    await bot.send_message(
        order["manager_id"],
        f"ℹ️ Замовлення **№{order_id}** взяв у роботу працівник"
        f" **{worker_name}**.",
    )
  except Exception:
    pass

  await callback.answer("Замовлення закріплено за вами!")


# --- ЗМІНА СТАТУСУ ЗАМОВЛЕННЯ ---
@router.callback_query(F.data.startswith("status_"))
async def change_status(callback: CallbackQuery):
  parts = callback.data.split("_")
  action = parts[1]  # assembling або done
  order_id = int(parts[2])

  order = get_order(order_id)
  if not order:
    await callback.answer("Замовлення не знайдено.", show_alert=True)
    return

  if order["worker_id"] != callback.from_user.id:
    await callback.answer(
        "Ви не є відповідальним за це замовлення!", show_alert=True
    )
    return

  worker_name = callback.from_user.full_name

  if action == "assembling":
    update_order_status(order_id, "Збирається")
    await callback.answer("Статус змінено на 'Збирається'")
    try:
      await bot.send_message(
          order["manager_id"],
          f"🟡 Замовлення **№{order_id}** переведено в статус **Збирається**"
          f" ({worker_name}).",
      )
    except Exception:
      pass

  elif action == "done":
    update_order_status(order_id, "Зібране")
    await callback.message.edit_text(
        f"✅ **Замовлення №{order_id}** — ЗІБРАНЕ\n\n{order['text']}"
    )
    try:
      await bot.send_message(
          order["manager_id"],
          f"🟢 Замовлення **№{order_id}** повністю **ЗІБРАНЕ** працівником"
          f" **{worker_name}**!",
      )
    except Exception:
      pass
    await callback.answer("Замовлення позначено як зібране!")


async def main():
  init_db()
  dp.include_router(router)
  await bot.delete_webhook(drop_pending_updates=True)
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
