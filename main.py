import asyncio
import logging
import os
import sys
import tempfile

from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message, User
from groq import AsyncGroq
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_FILE_SIZE = 25 * 1024 * 1024


class UserFormatter(logging.Formatter):
    @staticmethod
    def _format_arg(arg):
        if isinstance(arg, User):
            name = f"@{arg.username}" if arg.username else arg.full_name
            return f"User({arg.id}, {name})"
        return arg

    def format(self, record: logging.LogRecord) -> str:
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(self._format_arg(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: self._format_arg(v) for k, v in record.args.items()}
        return super().format(record)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(
    UserFormatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    BOT_TOKEN: SecretStr
    ALLOWED_USERS: list[int] = []
    GROQ_API_KEY: SecretStr
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()

if not settings.ALLOWED_USERS:
    raise ValueError("Пустой allowed_users. Ограничь доступ!")


bot = Bot(
    token=settings.BOT_TOKEN.get_secret_value(),
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
semaphore = asyncio.Semaphore(1)
groq = AsyncGroq(api_key=settings.GROQ_API_KEY.get_secret_value())


@asynccontextmanager
async def _tmp_audio_file(suffix: str = ".ogg"):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    tmp.close()
    try:
        yield tmp_path
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


async def _get_transcription(file_path: str) -> str:
    with open(file_path, "rb") as audio_file:
        transcription = await groq.audio.transcriptions.create(
            file=(file_path, audio_file.read()),
            model="whisper-large-v3-turbo",
            language="ru",
            response_format="text",
        )

    return transcription.strip() or "❌ Не удалось распознать речь."


@dp.message(CommandStart())
async def handle_start(message: Message):
    user = message.from_user
    is_allowed = user.id in settings.ALLOWED_USERS
    logger.info("Команда /start от %s, доступ=%s", user, is_allowed)

    text = f"👋 Привет!\n\nТвой Telegram ID: <code>{user.id}</code>"
    if not is_allowed:
        text += "\n\n⛔️ Нет доступа к боту."
    else:
        text += "\n\nПерешли мне голосовое сообщение, «кружок» или аудиофайл — я переведу их в текст."

    await message.answer(text)


@dp.message(F.voice | F.video_note | F.audio)
async def handle_audio(message: Message):
    user = message.from_user
    if user.id not in settings.ALLOWED_USERS:
        logger.warning("Отказ в доступе для %s", user)
        return

    media = message.voice or message.video_note or message.audio
    if media is None:
        logger.warning("Отсутствует аудиофайл")
        return

    logger.info(
        "Получено аудио от %s, длительность=%s сек",
        user, getattr(media, "duration", "unknown"), media.file_id,
    )

    if media.file_size is not None and media.file_size > MAX_FILE_SIZE:
        await message.reply("Файл слишком большой: лимит — 25 МБ.")
        return

    status_msg = await message.reply("⏳ В очереди на расшифровку...")

    async with semaphore:
        await status_msg.edit_text("🎙 Расшифровываю...")
        file_info = await bot.get_file(media.file_id)

        try:
            async with _tmp_audio_file(suffix=".ogg") as file_path:
                await bot.download_file(file_info.file_path, file_path)
                text = await _get_transcription(file_path)

            if len(text) <= 4096:
                await status_msg.edit_text(text)
            else:
                await status_msg.delete()
                for i in range(0, len(text), 4096):
                    await message.answer(text[i:i + 4096])

        except Exception as e:
            logger.exception("Ошибка при обработке аудио от %s", user)
            await status_msg.edit_text(f"⚠️ Ошибка: {e}", parse_mode=None)


async def main():
    logger.info("Запуск polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
