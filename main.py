import asyncio
import os
import tempfile
from contextlib import asynccontextmanager
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from faster_whisper import WhisperModel
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

model = WhisperModel(
    settings.whisper_model,
    device="cpu",
    compute_type="int8",
    cpu_threads=1
)

bot = Bot(token=settings.bot_token.get_secret_value())
dp = Dispatcher()
semaphore = asyncio.Semaphore(1)


@asynccontextmanager
async def temp_audio_file(suffix: str = ".ogg"):
    """Создает временный файл и гарантированно удаляет его при выходе из блока with."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    tmp.close()
    try:
        yield tmp_path
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@dp.message(F.voice | F.video_note | F.audio)
async def handle_audio(message: Message):
    if settings.allowed_users and message.from_user.id not in settings.allowed_users:
        return

    status_msg = await message.reply("⏳ В очереди на расшифровку...")

    async with semaphore:
        await status_msg.edit_text("🎙 Расшифровываю...")
        media = message.voice or message.video_note or message.audio
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
                        condition_on_previous_text=False
                    )
                )

                text = "".join(segment.text for segment in segments).strip()

            if text:
                for i in range(0, len(text), 4000):
                    chunk = text[i:i + 4000]
                    if i == 0:
                        await status_msg.edit_text(chunk)
                    else:
                        await message.reply(chunk)
            else:
                await status_msg.edit_text("❌ Речь не распознана.")

        except Exception as e:
            await status_msg.edit_text(f"⚠️ Ошибка: {e}")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
