import asyncio
from datetime import datetime, timedelta
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
            worker_id INTEGER,
            status TEXT,
            created_at TEXT,
            completed_at TEXT,
            photo_id TEXT,
            file_id TEXT,
            file_type TEXT
        )
    """)
  # Таблиця участі працівників у замовленнях (підтримка кількох виконавців, відсотків та коментарів)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            worker_id INTEGER,
            share_percent REAL DEFAULT 0,
            comment TEXT,
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
  created_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  cursor.execute(
      """
        INSERT INTO orders (text, manager_id, worker_id, status, created_at, completed_at, photo_id, file_id, file_type) 
        VALUES (?, ?, NULL, 'Нове', ?, NULL, ?, ?, ?)
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
        SELECT order_id, text, manager_id, worker_id, status, photo_id, file_id, file_type 
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
        "worker_id": row[3],
        "status": row[4],
        "photo_id": row[5],
        "file_id": row[6],
        "file_type": row[7],
    }
  return None


def get_order_assignees(order_id: int):
  """Повертає список кортежів (worker_id, full_name, share_percent, comment, completed_at)."""
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        SELECT oa.worker_id, u.full_name, oa.share_percent, oa.comment, oa.completed_at
        FROM order_assignments oa
        LEFT JOIN users u ON oa.worker_id = u.user_id
        WHERE oa.order_id = ?
    """,
      (order_id,),
  )
  rows = cursor.fetchall()
  conn.close()
  return rows


def get_total_assigned_percent(order_id: int, exclude_worker_id: int = None) -> float:
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  if exclude_worker_id is not None:
    cursor.execute(
        """
            SELECT COALESCE(SUM(share_percent), 0)
            FROM order_assignments
            WHERE order_id = ? AND worker_id != ?
        """,
        (order_id, exclude_worker_id),
    )
  else:
    cursor.execute(
        """
            SELECT COALESCE(SUM(share_percent), 0)
            FROM order_assignments
            WHERE order_id = ?
        """,
        (order_id,),
    )
  total = cursor.fetchone()[0]
  conn.close()
  return float(total or 0)


def join_order_work(order_id: int, worker_id: int) -> bool:
  """Додає працівника до виконавців замовлення (дозволяє працювати кільком людям одночасно)."""
  order = get_order(order_id)
  if not order or order["status"] == "Зібране":
    return False

  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        INSERT OR IGNORE INTO order_assignments (order_id, worker_id, share_percent, comment, completed_at)
        VALUES (?, ?, 0, NULL, NULL)
    """,
      (order_id, worker_id),
  )

  # Якщо замовлення ще було 'Нове', переводимо в 'Збирається' і фіксуємо першого відповідального
  if order["status"] == "Нове":
    cursor.execute(
        """
            UPDATE orders 
            SET status = 'Збирається', worker_id = COALESCE(worker_id, ?)
            WHERE order_id = ?
        """,
        (worker_id, order_id),
    )
  else:
    cursor.execute(
        """
            UPDATE orders 
            SET status = 'Збирається'
            WHERE order_id = ?
        """,
        (order_id,),
    )
  conn.commit()
  conn.close()
  return True


def save_worker_completion(
    order_id: int, worker_id: int, percent: float, comment: str
) -> dict:
  """Зберігає відсоток і коментар працівника. Якщо сума відсотків >= 100%, закриває замовлення."""
  now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        INSERT INTO order_assignments (order_id, worker_id, share_percent, comment, completed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(order_id, worker_id) DO UPDATE SET
            share_percent = excluded.share_percent,
            comment = excluded.comment,
            completed_at = excluded.completed_at
    """,
      (order_id, worker_id, percent, comment, now_str),
  )
  conn.commit()

  cursor.execute(
      """
        SELECT COALESCE(SUM(share_percent), 0)
        FROM order_assignments
        WHERE order_id = ?
    """,
      (order_id,),
  )
  total_percent = float(cursor.fetchone()[0] or 0)

  is_completed = total_percent >= 99.99
  if is_completed:
    cursor.execute(
        """
            UPDATE orders
            SET status = 'Зібране', completed_at = ?
            WHERE order_id = ?
        """,
        (now_str, order_id),
    )
  else:
    cursor.execute(
        """
            UPDATE orders
            SET status = 'Збирається'
            WHERE order_id = ?
        """,
        (order_id,),
    )
  conn.commit()
  conn.close()

  return {"total_percent": total_percent, "is_completed": is_completed}


def get_worker_active_orders(worker_id: int):
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        SELECT DISTINCT o.order_id, o.text, o.status, o.photo_id, o.file_id, o.file_type
        FROM orders o
        LEFT JOIN order_assignments oa ON o.order_id = oa.order_id AND oa.worker_id = ?
        WHERE o.status != 'Зібране' AND (o.worker_id = ? OR oa.worker_id = ?)
    """,
      (worker_id, worker_id, worker_id),
  )
  rows = cursor.fetchall()
  conn.close()
  return rows


def get_warehouse_stats():
  """Рахує сумарну частку зібраних замовлень (сума share_percent / 100) по кожному працівнику."""
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()
  cursor.execute("""
        SELECT u.full_name, COALESCE(SUM(oa.share_percent) / 100.0, 0.0) as total_orders
        FROM users u
        LEFT JOIN order_assignments oa ON u.user_id = oa.worker_id AND oa.share_percent > 0
        WHERE u.role = 'worker'
        GROUP BY u.user_id
        ORDER BY total_orders DESC
    """)
  rows = cursor.fetchall()
  conn.close()
  return rows


def get_period_report_by_worker(days: int | None):
  """
    Повертає звіт за період по кожному працівнику:
    {worker_id: {"name": ..., "total_share": ..., "items": [(order_id, percent, comment, completed_at, order_text), ...]}}
    Якщо days is None — за весь час.
    """
  conn = sqlite3.connect("warehouse_bot.db")
  cursor = conn.cursor()

  if days is not None:
    cutoff = (datetime.now() - timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    cursor.execute(
        """
            SELECT u.user_id, u.full_name, oa.order_id, oa.share_percent, oa.comment, oa.completed_at, o.text
            FROM users u
            LEFT JOIN order_assignments oa
                ON u.user_id = oa.worker_id
                AND oa.share_percent > 0
                AND oa.completed_at >= ?
            LEFT JOIN orders o ON oa.order_id = o.order_id
            WHERE u.role = 'worker'
            ORDER BY u.full_name ASC, oa.completed_at DESC
        """,
        (cutoff,),
    )
  else:
    cursor.execute("""
            SELECT u.user_id, u.full_name, oa.order_id, oa.share_percent, oa.comment, oa.completed_at, o.text
            FROM users u
            LEFT JOIN order_assignments oa
                ON u.user_id = oa.worker_id
                AND oa.share_percent > 0
            LEFT JOIN orders o ON oa.order_id = o.order_id
            WHERE u.role = 'worker'
            ORDER BY u.full_name ASC, oa.completed_at DESC
        """)

  rows = cursor.fetchall()
  conn.close()

  report = {}
  for (
      worker_id,
      full_name,
      order_id,
      share_percent,
      comment,
      completed_at,
      order_text,
  ) in rows:
    if worker_id not in report:
      report[worker_id] = {
          "name": full_name or f"ID {worker_id}",
          "total_share": 0.0,
          "items": [],
      }
    if order_id is not None and share_percent:
      pct = float(share_percent)
      report[worker_id]["total_share"] += pct / 100.0
      report[worker_id]["items"].append(
          (order_id, pct, comment or "—", completed_at or "", order_text or "")
      )

  return report


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
    assignees = get_order_assignees(order_id)
    result.append((order_id, text, status, created_at, assignees))
  return result


# --- КЛАВІАТУРИ ---
def get_manager_menu():
  return ReplyKeyboardMarkup(
      keyboard=[
          [KeyboardButton(text="➕ Нове замовлення")],
          [
              KeyboardButton(text="📊 Статистика складу"),
              KeyboardButton(text="📅 Звіт за період"),
          ],
          [KeyboardButton(text="📜 Усі замовлення за весь час")],
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
  waiting_for_completion_percent = State()
  waiting_for_completion_comment = State()


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

  text = "📊 **Статистика збирання замовлень (з урахуванням % участі):**\n\n"
  for worker_name, total_orders in stats:
    text += (
        f"👤 **{worker_name}** — зібрано: `{total_orders:.2f}` замовлень\n"
    )

  await message.answer(text, parse_mode="Markdown")


# --- ЗВІТ ЗА ПЕРІОД ПО КОЖНІЙ ЛЮДИНІ ---
@router.message(F.text == "📅 Звіт за період")
async def choose_period_report(message: Message):
  if get_user_role(message.from_user.id) != "manager":
    await message.answer("Ця функція доступна лише менеджерам.")
    return

  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="📆 Сьогодні (1 день)", callback_data="period_1"
              ),
              InlineKeyboardButton(
                  text="🗓 7 днів", callback_data="period_7"
              ),
          ],
          [
              InlineKeyboardButton(
                  text="🗓 30 днів", callback_data="period_30"
              ),
              InlineKeyboardButton(
                  text="♾ За весь час", callback_data="period_all"
              ),
          ],
      ]
  )
  await message.answer("Оберіть період для звіту по працівниках:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("period_"))
async def show_period_report(callback: CallbackQuery):
  if get_user_role(callback.from_user.id) != "manager":
    await callback.answer("Ця функція доступна лише менеджерам.", show_alert=True)
    return

  raw_period = callback.data.split("_")[1]
  days = None if raw_period == "all" else int(raw_period)
  period_titles = {
      "1": "за останню добу",
      "7": "за останні 7 днів",
      "30": "за останні 30 днів",
      "all": "за весь час",
  }

  report = get_period_report_by_worker(days)
  if not report:
    await callback.message.edit_text("Немає зареєстрованих працівників.")
    await callback.answer()
    return

  text = f"📅 **Звіт по працівниках ({period_titles.get(raw_period, '')}):**\n\n"
  for _, info in report.items():
    text += f"👷 **{info['name']}** — разом: `{info['total_share']:.2f}` замовлень\n"
    if not info["items"]:
      text += "   ▫️ За цей період виконаних частин немає\n"
    else:
      for order_id, pct, comment, completed_at, _ in info["items"]:
        short_date = completed_at[:16] if completed_at else ""
        text += (
            f"   • №{order_id} (`{pct:g}%`) — _{comment}_ ({short_date})\n"
        )
    text += "-----------------------------------\n"

  if len(text) > 4000:
    text = text[:4000] + "\n\n*(Звіт занадто великий, показано частину)*"

  await callback.message.edit_text(text, parse_mode="Markdown")
  await callback.answer()


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

  for order_id, order_text, status, created_at, assignees in orders:
    short_text = (
        order_text[:35] + "..." if len(order_text) > 35 else order_text
    )
    text += f"🆔 **№{order_id}** | Статус: *{status}*\n"
    text += f"📅 Створено: {created_at[:16]}\n"
    text += f"📝 {short_text}\n"

    if assignees:
      for _, w_name, pct, comm, _ in assignees:
        w_label = w_name or "Працівник"
        if pct and pct > 0:
          text += f"   👷 {w_label}: `{pct:g}%` | 💬 _{comm or 'без коментаря'}_\n"
        else:
          text += f"   👷 {w_label}: у роботі\n"
    else:
      text += "   👷 Ще ніхто не взяв\n"

    if role == "worker" and status != "Зібране":
      inline_buttons.append([
          InlineKeyboardButton(
              text=f"📥 Взяти №{order_id}", callback_data=f"take_{order_id}"
          ),
          InlineKeyboardButton(
              text=f"🟢 Зібране №{order_id}", callback_data=f"done_{order_id}"
          ),
      ])

    text += "-----------------------------------\n"

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
    assignees = get_order_assignees(order_id)
    team_str = (
        ", ".join(
            [
                f"{a[1] or 'Працівник'} ({a[2]:g}%)"
                for a in assignees
                if a[2] and a[2] > 0
            ]
        )
        or "в роботі"
    )
    msg_text = (
        f"📦 **Замовлення №{order_id}** | Статус: _{status}_\n"
        f"👥 Учасники: {team_str}\n\n{text}"
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


# --- ВЗЯТТЯ ЗАМОВЛЕННЯ В РОБОТУ (ПІДТРИМУЄ КІЛЬКОХ ВИКОНАВЦІВ) ---
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
  assignees = get_order_assignees(order_id)
  names_list = ", ".join([a[1] or "Працівник" for a in assignees])

  updated_body = (
      f"📦 **Замовлення №{order_id}** | Статус: _Збирається_\n"
      f"👷 Виконавці: **{names_list}**\n\n{order['text']}"
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
        f" **{worker_name}** (разом: {names_list}).",
    )
  except Exception:
    pass

  await callback.answer("Вас додано до виконавців замовлення!")


# --- ПОЧАТОК ЗАВЕРШЕННЯ ЗАМОВЛЕННЯ: ЗАПИТ ВІДСОТКА УЧАСТІ ---
@router.callback_query(F.data.startswith("done_"))
async def prompt_completion_percent(callback: CallbackQuery, state: FSMContext):
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

  # Гарантуємо, що працівник зафіксований як учасник
  join_order_work(order_id, callback.from_user.id)

  other_percent = get_total_assigned_percent(
      order_id, exclude_worker_id=callback.from_user.id
  )
  remaining = max(0.0, 100.0 - other_percent)

  await state.update_data(complete_order_id=order_id)
  await state.set_state(OrderStates.waiting_for_completion_percent)

  hint = (
      f"Вже розподілено між іншими: `{other_percent:g}%` (залишок до 100%:"
      f" `{remaining:g}%`).\n"
      if other_percent > 0
      else ""
  )
  await callback.message.answer(
      f"📊 **Замовлення №{order_id}**\n{hint}"
      "Введіть ваш **відсоток виконання** цього замовлення числом від `1` до"
      " `100` (наприклад: `100`, `70` або `30`):",
      parse_mode="Markdown",
  )
  await callback.answer()


@router.message(OrderStates.waiting_for_completion_percent)
async def process_completion_percent(message: Message, state: FSMContext):
  raw = (message.text or "").replace("%", "").replace(",", ".").strip()
  try:
    percent = float(raw)
  except ValueError:
    await message.answer(
        "Будь ласка, введіть коректне число від 1 до 100 (наприклад, `50` або"
        " `100`).",
        parse_mode="Markdown",
    )
    return

  if percent <= 0 or percent > 100:
    await message.answer("Відсоток має бути більшим за 0 і не більшим за 100.")
    return

  data = await state.get_data()
  order_id = data.get("complete_order_id")
  other_percent = get_total_assigned_percent(
      order_id, exclude_worker_id=message.from_user.id
  )
  if other_percent + percent > 100.01:
    allowed = max(0.0, 100.0 - other_percent)
    await message.answer(
        f"Сума відсотків по замовленню №{order_id} перевищить 100% (вже є"
        f" `{other_percent:g}%`). Введіть число до `{allowed:g}`:",
        parse_mode="Markdown",
    )
    return

  await state.update_data(completion_percent=percent)
  await state.set_state(OrderStates.waiting_for_completion_comment)
  await message.answer(
      "💬 Введіть **коментар** до зібраного замовлення (наприклад: кількість"
      " палет, розмір посилки або примітку):",
      parse_mode="Markdown",
  )


@router.message(OrderStates.waiting_for_completion_comment)
async def process_completion_comment(message: Message, state: FSMContext):
  comment = (message.text or "").strip()
  if not comment:
    await message.answer(
        "Коментар не може бути порожнім. Вкажіть, наприклад, кількість палет"
        " або розмір посилки:"
    )
    return

  data = await state.get_data()
  order_id = data.get("complete_order_id")
  percent = float(data.get("completion_percent", 100.0))
  await state.clear()

  order = get_order(order_id)
  if not order:
    await message.answer(
        "Замовлення не знайдено.", reply_markup=get_worker_menu()
    )
    return

  res = save_worker_completion(order_id, message.from_user.id, percent, comment)
  worker_name = message.from_user.full_name
  assignees = get_order_assignees(order_id)

  breakdown_lines = []
  for _, w_name, pct, comm, _ in assignees:
    if pct and pct > 0:
      breakdown_lines.append(
          f"• {w_name or 'Працівник'}: `{pct:g}%` — _{comm or '—'}_"
      )
  breakdown_text = "\n".join(breakdown_lines)

  if res["is_completed"]:
    await message.answer(
        f"✅ **Замовлення №{order_id}** позначено як **ЗІБРАНЕ** (сума:"
        f" `{res['total_percent']:g}%`).\n"
        f"Ваш внесок: `{percent:g}%` | Коментар: _{comment}_",
        parse_mode="Markdown",
        reply_markup=get_worker_menu(),
    )
    try:
      await bot.send_message(
          order["manager_id"],
          f"🟢 Замовлення **№{order_id}** повністю **ЗІБРАНЕ**!\n"
          f"Розподіл та коментарі:\n{breakdown_text}",
          parse_mode="Markdown",
      )
    except Exception:
      pass
  else:
    await message.answer(
        f"🟡 Ваш внесок `{percent:g}%` та коментар збережено для замовлення"
        f" **№{order_id}**.\n"
        f"Загальний відсоток виконання зараз: `{res['total_percent']:g}%` з"
        " `100%` (замовлення лишається в роботі до досягнення 100%).",
        parse_mode="Markdown",
        reply_markup=get_worker_menu(),
    )
    try:
      await bot.send_message(
          order["manager_id"],
          f"🟡 Працівник **{worker_name}** вніс `{percent:g}%` по замовленню"
          f" **№{order_id}** (разом `{res['total_percent']:g}%`).\n"
          f"💬 Коментар: _{comment}_",
          parse_mode="Markdown",
      )
    except Exception:
      pass


async def main():
  init_db()
  dp.include_router(router)
  await bot.delete_webhook(drop_pending_updates=True)
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
