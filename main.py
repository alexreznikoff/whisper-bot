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
from faster_whisper import WhisperModel
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    bot_token: SecretStr
    allowed_users: list[int] = []
    whisper_model: str = "medium"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()

if not settings.allowed_users:
    raise ValueError("Пустой allowed_users. Ограничь доступ!")


logger.info("Загрузка модели Whisper '%s' (device=cpu, int8)...", settings.whisper_model)
model = WhisperModel(
    settings.whisper_model,
    device="cpu",
    compute_type="int8",
    cpu_threads=1
)
logger.info("Модель успешно загружена.")

bot = Bot(
    token=settings.bot_token.get_secret_value(),
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
semaphore = asyncio.Semaphore(1)


@asynccontextmanager
async def temp_audio_file(suffix: str = ".ogg"):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    tmp.close()
    try:
        yield tmp_path
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@dp.message(CommandStart())
async def handle_start(message: Message):
    user = message.from_user
    is_allowed = user.id in settings.allowed_users
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
    if user.id not in settings.allowed_users:
        logger.warning("Отказ в доступе для user_id=%s (@%s)", user.id, user.username)
        return

    media = message.voice or message.video_note or message.audio
    logger.info(
        "Получено аудио от %s, длительность=%s сек, file_id=%s",
        user, getattr(media, "duration", "unknown"), media.file_id,
    )

    status_msg = await message.reply("⏳ В очереди на расшифровку...")

    async with semaphore:
        await status_msg.edit_text("🎙 Расшифровываю...")
        file_info = await bot.get_file(media.file_id)

        try:
            async with temp_audio_file(suffix=".ogg") as file_path:
                await bot.download_file(file_info.file_path, file_path)

                loop = asyncio.get_running_loop()
                segments, _ = await loop.run_in_executor(
                    None,
                    lambda: model.transcribe(
                        file_path,
                        language="ru",
                        beam_size=2,
                        vad_filter=True,
                        condition_on_previous_text=False,
                    )
                )

                text = "".join(segment.text for segment in segments).strip()

            logger.info("Расшифровка завершена для %s (символов: %d)", user, len(text))

            if text:
                for i in range(0, len(text), 4000):
                    chunk = text[i:i + 4000]
                    if i == 0:
                        await status_msg.edit_text(chunk, parse_mode=None)
                    else:
                        await message.reply(chunk, parse_mode=None)
            else:
                await status_msg.edit_text("❌ Речь не распознана.")

        except Exception as e:
            logger.exception("Ошибка при обработке аудио от %s", user)
            await status_msg.edit_text(f"⚠️ Ошибка: {e}", parse_mode=None)


async def main():
    logger.info("Запуск polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
