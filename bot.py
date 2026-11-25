import asyncio
from datetime import datetime
import logging
import sqlite3
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

# Твій токен
TOKEN = "9033504163:AAFAYrGLgkS0G3YfS-pTkWZOYs7tYBpD-6w"

logging.basicConfig(level=logging.INFO)
router = Router()
bot = Bot(token=TOKEN)
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
            status TEXT,
            created_at TEXT,
            completed_at TEXT,
            photo_id TEXT,
            file_id TEXT,
            file_type TEXT
        )
    """)
  # Кілька виконавців на одне замовлення
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_workers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            worker_id INTEGER,
            is_done INTEGER DEFAULT 0,
            completed_at TEXT,
            UNIQUE(order_id, worker_id)
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


def create_order(
    text: str,
    manager_id: int,
    photo_id: str = None,
    file_id: str = None,
    file_type: str = None,
) -> int:
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  created_time = datetime.now().strftime("%Y-%m-%d %H:%M")
  cursor.execute(
      """
        INSERT INTO orders (text, manager_id, status, created_at, completed_at, photo_id, file_id, file_type) 
        VALUES (?, ?, 'Нове', ?, NULL, ?, ?, ?)
    """,
      (text, manager_id, created_time, photo_id, file_id, file_type),
  )
  order_id = cursor.lastrowid
  conn.commit()
  conn.close()
  return order_id


def get_order(order_id: int):
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        SELECT order_id, text, manager_id, status, created_at, completed_at, photo_id, file_id, file_type 
        FROM orders WHERE order_id = ?
    """,
      (order_id,),
  )
  row = cursor.fetchone()
  conn.close()
  if row:
    return {
        "order_id": row[0],
        "text": row[1],
        "manager_id": row[2],
        "status": row[3],
        "created_at": row[4],
        "completed_at": row[5],
        "photo_id": row[6],
        "file_id": row[7],
        "file_type": row[8],
    }
  return None


def get_order_workers(order_id: int):
  """Повертає список кортежів (worker_id, full_name, is_done)."""
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        SELECT ow.worker_id, u.full_name, ow.is_done
        FROM order_workers ow
        LEFT JOIN users u ON ow.worker_id = u.user_id
        WHERE ow.order_id = ?
    """,
      (order_id,),
  )
  rows = cursor.fetchall()
  conn.close()
  return rows


def join_order_work(order_id: int, worker_id: int) -> bool:
  """Додає працівника до виконавців замовлення."""
  order = get_order(order_id)
  if not order or order["status"] == "Зібране":
    return False

  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        INSERT OR IGNORE INTO order_workers (order_id, worker_id, is_done)
        VALUES (?, ?, 0)
    """,
      (order_id, worker_id),
  )
  cursor.execute(
      """
        UPDATE orders SET status = 'Збирається' WHERE order_id = ? AND status = 'Нове'
    """,
      (order_id,),
  )
  conn.commit()
  conn.close()
  return True


def mark_order_done_by_worker(order_id: int, worker_id: int):
  """Фіксує завершення замовлення працівником і переводить замовлення у статус 'Зібране'."""
  now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        INSERT INTO order_workers (order_id, worker_id, is_done, completed_at)
        VALUES (?, ?, 1, ?)
        ON CONFLICT(order_id, worker_id) DO UPDATE SET
            is_done = 1,
            completed_at = COALESCE(completed_at, excluded.completed_at)
    """,
      (order_id, worker_id, now_str),
  )
  cursor.execute(
      """
        UPDATE orders
        SET status = 'Зібране', completed_at = ?
        WHERE order_id = ?
    """,
      (now_str, order_id),
  )
  conn.commit()
  conn.close()


def get_worker_active_orders(worker_id: int):
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        SELECT DISTINCT o.order_id, o.text, o.status, o.photo_id, o.file_id, o.file_type
        FROM orders o
        JOIN order_workers ow ON o.order_id = ow.order_id
        WHERE ow.worker_id = ? AND ow.is_done = 0 AND o.status != 'Зібране'
    """,
      (worker_id,),
  )
  rows = cursor.fetchall()
  conn.close()
  return rows


def get_warehouse_stats():
  """Рахує, скільки замовлень зібрав кожен працівник."""
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute("""
        SELECT u.full_name, COUNT(ow.order_id) as total_done
        FROM users u
        LEFT JOIN order_workers ow ON u.user_id = ow.worker_id AND ow.is_done = 1
        WHERE u.role = 'worker'
        GROUP BY u.user_id
        ORDER BY total_done DESC
    """)
  rows = cursor.fetchall()
  conn.close()
  return rows


def get_filtered_orders(filter_type: str):
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()

  if filter_type == "active":
    cursor.execute("""
            SELECT order_id, text, status, created_at
            FROM orders
            WHERE status != 'Зібране'
            ORDER BY order_id DESC
        """)
  elif filter_type == "done":
    cursor.execute("""
            SELECT order_id, text, status, created_at
            FROM orders
            WHERE status = 'Зібране'
            ORDER BY order_id DESC
        """)
  else:
    cursor.execute("""
            SELECT order_id, text, status, created_at
            FROM orders
            ORDER BY order_id DESC
            LIMIT 25
        """)

  rows = cursor.fetchall()
  conn.close()

  result = []
  for order_id, text, status, created_at in rows:
    workers = get_order_workers(order_id)
    result.append((order_id, text, status, created_at, workers))
  return result


# --- КЛАВІАТУРИ ---
def get_manager_menu():
  return ReplyKeyboardMarkup(
      keyboard=[
          [KeyboardButton(text="➕ Нове замовлення")],
          [
              KeyboardButton(text="📊 Статистика складу"),
              KeyboardButton(text="📜 Усі замовлення за весь час"),
          ],
          [KeyboardButton(text="🔄 Змінити роль")],
      ],
      resize_keyboard=True,
  )


def get_worker_menu():
  return ReplyKeyboardMarkup(
      keyboard=[
          [KeyboardButton(text="📋 Мої активні замовлення")],
          [KeyboardButton(text="📜 Усі завдання складу")],
          [KeyboardButton(text="🔄 Змінити роль")],
      ],
      resize_keyboard=True,
  )


def get_order_action_keyboard(order_id: int):
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="📥 Взяти в роботу", callback_data=f"take_{order_id}"
              ),
              InlineKeyboardButton(
                  text="🟢 Зібране", callback_data=f"done_{order_id}"
              ),
          ]
      ]
  )


# --- СТАНИ ДЛЯ FSM ---
class OrderStates(StatesGroup):
  waiting_for_order_content = State()


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
      "Надішліть деталі замовлення: текст, фото або файл (можна разом із"
      " підписом):"
  )
  await state.set_state(OrderStates.waiting_for_order_content)


@router.message(OrderStates.waiting_for_order_content, F.photo | F.document | F.text)
async def process_order_content(message: Message, state: FSMContext):
  text = (
      message.caption
      if message.caption
      else (message.text if message.text else "Без опису")
  )

  photo_id = None
  file_id = None
  file_type = None

  if message.photo:
    photo_id = message.photo[-1].file_id
    file_type = "photo"
  elif message.document:
    file_id = message.document.file_id
    file_type = "document"

  order_id = create_order(
      text, message.from_user.id, photo_id, file_id, file_type
  )
  await state.clear()

  await message.answer(
      f"✅ Замовлення **№{order_id}** створено та відправлено на склад!",
      reply_markup=get_manager_menu(),
  )

  keyboard = get_order_action_keyboard(order_id)
  workers = get_all_workers()
  for worker_id in workers:
    try:
      caption_text = f"📦 **Нове замовлення №{order_id}**\n\n{text}"
      if photo_id:
        await bot.send_photo(
            worker_id, photo=photo_id, caption=caption_text, reply_markup=keyboard
        )
      elif file_id:
        await bot.send_document(
            worker_id,
            document=file_id,
            caption=caption_text,
            reply_markup=keyboard,
        )
      else:
        await bot.send_message(
            worker_id, text=caption_text, reply_markup=keyboard
        )
    except Exception:
      pass


# --- СТАТИСТИКА СКЛАДУ ---
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
    text += f"👤 **{worker_name}** — зібрано: `{total_done}`\n"

  await message.answer(text, parse_mode="Markdown")


# --- ВИБІР ФІЛЬТРУ ІСТОРІЇ ---
@router.message(
    F.text.in_({"📜 Усі замовлення за весь час", "📜 Усі завдання складу"})
)
async def choose_order_filter(message: Message):
  role = get_user_role(message.from_user.id)
  if role not in ["manager", "worker"]:
    await message.answer("Будь ласка, оберіть вашу роль через /start.")
    return

  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="🟡 Активні", callback_data="history_active"
              ),
              InlineKeyboardButton(
                  text="🟢 Виконані", callback_data="history_done"
              ),
          ],
          [InlineKeyboardButton(text="📚 Усі замовлення", callback_data="history_all")],
      ]
  )
  await message.answer(
      "Оберіть категорію замовлень для перегляду:", reply_markup=keyboard
  )


# --- ОБРОБКА НАТИСКАННЯ ФІЛЬТРІВ ІСТОРІЇ ---
@router.callback_query(F.data.startswith("history_"))
async def show_filtered_history(callback: CallbackQuery):
  role = get_user_role(callback.from_user.id)
  if role not in ["manager", "worker"]:
    await callback.answer("Дія недоступна.", show_alert=True)
    return

  filter_type = callback.data.split("_")[1]
  orders = get_filtered_orders(filter_type)

  if not orders:
    await callback.message.edit_text(
        "📭 За таким фільтром замовлень не знайдено."
    )
    await callback.answer()
    return

  titles = {
      "active": "🟡 Активні замовлення:",
      "done": "🟢 Виконані замовлення:",
      "all": "📚 Усі замовлення за весь час:",
  }

  text = f"📜 **{titles.get(filter_type, 'Історія')}**\n\n"
  inline_buttons = []

  for order_id, order_text, status, created_at, workers in orders:
    short_text = (
        order_text[:35] + "..." if len(order_text) > 35 else order_text
    )
    workers_str = (
        ", ".join([w[1] or "Працівник" for w in workers])
        if workers
        else "Ще ніхто не взяв"
    )
    text += f"🆔 **№{order_id}** | Статус: *{status}*\n"
    text += f"📅 Створено: {created_at}\n"
    text += f"👷 Виконавці: {workers_str}\n"
    text += f"📝 {short_text}\n"
    text += "-----------------------------------\n"

    if role == "worker" and status != "Зібране":
      inline_buttons.append([
          InlineKeyboardButton(
              text=f"📥 Взяти №{order_id}", callback_data=f"take_{order_id}"
          ),
          InlineKeyboardButton(
              text=f"🟢 Зібране №{order_id}", callback_data=f"done_{order_id}"
          ),
      ])

  keyboard = (
      InlineKeyboardMarkup(inline_keyboard=inline_buttons)
      if inline_buttons
      else None
  )

  if len(text) > 4000:
    text = text[:4000] + "\n\n*(Список занадто довгий, показано частину)*"

  await callback.message.edit_text(
      text, parse_mode="Markdown", reply_markup=keyboard
  )
  await callback.answer()


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

  for order_id, text, status, photo_id, file_id, file_type in active_orders:
    workers = get_order_workers(order_id)
    workers_str = ", ".join([w[1] or "Працівник" for w in workers]) or "в роботі"
    msg_text = (
        f"📦 **Замовлення №{order_id}** | Статус: _{status}_\n"
        f"👷 Виконавці: {workers_str}\n\n{text}"
    )
    keyboard = get_order_action_keyboard(order_id)
    if photo_id:
      await message.answer_photo(
          photo=photo_id, caption=msg_text, reply_markup=keyboard
      )
    elif file_id:
      await message.answer_document(
          document=file_id, caption=msg_text, reply_markup=keyboard
      )
    else:
      await message.answer(text=msg_text, reply_markup=keyboard)


# --- ВЗЯТТЯ ЗАМОВЛЕННЯ В РОБОТУ (КІЛЬКА ВИКОНАВЦІВ) ---
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

  if order["status"] == "Зібране":
    await callback.answer("Це замовлення вже зібране.", show_alert=True)
    return

  join_order_work(order_id, callback.from_user.id)
  worker_name = callback.from_user.full_name
  workers = get_order_workers(order_id)
  workers_str = ", ".join([w[1] or "Працівник" for w in workers])

  updated_body = (
      f"📦 **Замовлення №{order_id}** | Статус: _Збирається_\n"
      f"👷 Виконавці: **{workers_str}**\n\n{order['text']}"
  )
  keyboard = get_order_action_keyboard(order_id)

  try:
    if callback.message.photo or callback.message.document:
      await callback.message.edit_caption(
          caption=updated_body, reply_markup=keyboard
      )
    elif callback.message.text and callback.message.text.startswith(
        f"📦 **Замовлення №{order_id}**"
    ):
      await callback.message.edit_text(
          text=updated_body, reply_markup=keyboard
      )
  except Exception:
    pass

  try:
    await bot.send_message(
        order["manager_id"],
        f"ℹ️ До збирання замовлення **№{order_id}** приєднався працівник"
        f" **{worker_name}** (разом: {workers_str}).",
    )
  except Exception:
    pass

  await callback.answer("Вас додано до виконавців замовлення!")


# --- ПОЗНАЧЕННЯ ЗАМОВЛЕННЯ ЯК ЗІБРАНЕ ---
@router.callback_query(F.data.startswith("done_"))
async def mark_order_done(callback: CallbackQuery):
  if get_user_role(callback.from_user.id) != "worker":
    await callback.answer(
        "Тільки складські працівники можуть позначати замовлення зібраними!",
        show_alert=True,
    )
    return

  order_id = int(callback.data.split("_")[1])
  order = get_order(order_id)
  if not order:
    await callback.answer("Замовлення не знайдено.", show_alert=True)
    return

  if order["status"] == "Зібране":
    await callback.answer("Це замовлення вже позначене як зібране.", show_alert=True)
    return

  worker_name = callback.from_user.full_name
  mark_order_done_by_worker(order_id, callback.from_user.id)
  workers = get_order_workers(order_id)
  workers_str = ", ".join([w[1] or "Працівник" for w in workers])

  done_text = (
      f"✅ **Замовлення №{order_id}** — ЗІБРАНЕ\n"
      f"👷 Виконавці: **{workers_str}**\n\n{order['text']}"
  )

  try:
    if callback.message.photo or callback.message.document:
      await callback.message.edit_caption(caption=done_text, reply_markup=None)
    elif callback.message.text and callback.message.text.startswith(
        f"📦 **Замовлення №{order_id}**"
    ):
      await callback.message.edit_text(text=done_text, reply_markup=None)
  except Exception:
    pass

  try:
    await bot.send_message(
        order["manager_id"],
        f"🟢 Замовлення **№{order_id}** позначив як **ЗІБРАНЕ** працівник"
        f" **{worker_name}** (виконавці: {workers_str}).",
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
