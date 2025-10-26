"""
OpenAI GPT service for generating sports commentary.

This module provides functionality to generate dynamic sports commentary
using OpenAI's GPT models for the Bauman United football team.
"""

import logging
from typing import List, Optional
from openai import OpenAI
from config.settings import Config

logger = logging.getLogger(__name__)


class GPTCommentaryService:
    """Service for generating sports commentary using OpenAI GPT."""
    
    def __init__(self):
        """Initialize the GPT service."""
        self.config = Config()
        if not self.config.is_openai_configured:
            raise ValueError("OpenAI API key not configured")
        
        self.client = OpenAI(api_key=self.config.OPENAI_KEY)
        self.model = "gpt-4"  # Using GPT-4 for better quality
    
    def generate_commentary(
        self, 
        previous_messages: List[str], 
        new_score: str, 
        is_our_goal: bool = True,
        scorer_surname: str = None
    ) -> Optional[str]:
        """
        Generate sports commentary for a score change.
        
        Args:
            previous_messages: List of previous score change messages
            new_score: New score in format "2:1" or "1-1"
            is_our_goal: Whether our team scored (True) or opponent scored (False)
            scorer_surname: Surname of the player who scored (if our team scored)
            
        Returns:
            Generated commentary message or None if generation failed
        """
        try:
            # Format previous messages for context
            context_messages = "\n".join([f'"{msg}"' for msg in previous_messages])
            
            # Format scorer information
            scorer_info = scorer_surname if scorer_surname else "пустой"
            
            # Create the prompt
            prompt = f"""Ты — футбольный комментатор Telegram-канала любительской команды Bauman United.
Твоя задача — написать короткое, эмоциональное сообщение о смене счёта в матче.

Контекст: тебе будет передан список предыдущих сообщений о счёте и событиях матча (включая фамилии игроков, которые уже забивали).
{context_messages}

Параметры:
Новый счет и фамилия забившего (если есть) - {new_score} | {scorer_info}
score — текущий счёт, где первая цифра — Bauman United, вторая — соперник (например, 2:1).

scorer — фамилия игрока, если гол забила Bauman United; если гол забил соперник — параметр пустой.

🔧 Правила:

Если scorer указан → забили мы.

Упомяни фамилию игрока.

Если из контекста видно, что он уже забивал — отметь это (варианты: оформил дубль, второй сегодня, снова отличился, вот это серия, третий в копилку и т.д.).

Не описывай, как именно забили.

Передай радость, драйв, уверенность.

Если scorer пустой → забил соперник.

Не выдумывай имён.

Используй самоиронию, лёгкое раздражение или подбадривание.

Можно добавить фразы вроде "не удержали", "ай, обидно", "ну, теперь погнали отыгрываться!"

Не придумывай события, которых не было в контексте.

Сообщение должно быть коротким (1–3 предложения), эмоциональным и живым.

Пиши в стиле футбольного чата, без официальщины.

Эмодзи можно использовать свободно (⚽🔥😱💪😤🙌😅 и т.п.).

Без хэштегов.

🧩 Возможные прозвища игроков (можно использовать иногда вместо фамилии):

Богомолов — Ега, Министр Обороны

Писарев — Писарь, Кракен

Королёв — Король

Шевченко — Шева

Калькаев — Калькай

Планидин — Гера

Захаров — Левыч

Жарких — Жар

Заночуев — Капитан, El Capitano

Селифанов — Селифан

Шведов — Швед

Клочков — Колач

Клейменов — Клейменыч

Шурупов — Шуруп

Молотков — Костян

Панферов — Панфер

Поляшов — Поляш

Яковлев — Ярик

Прокопенко — Прокоп

💬 Примеры фраз, в духе которых нужно писать:

"Пошла жара! 🔥"
"Ну наконец-то! 💪"
"Ай, досадно… но ничего, камбэк грядёт 😤"
"Вот это поворот! 😱"
"Дубль! Этот парень сегодня в огне! 🔥🔥"
"Игра пошла на нервах, держимся до конца 💥"
"Пошла тёпленькая!"

💡 Примеры логики:

Наш первый гол: "ГОООЛ! 🔥 {scorer_surname or 'Игрок'} открывает счёт — {new_score}!"

Наш второй (дубль): "Дубль! {scorer_surname or 'Игрок'} снова отличился — {new_score}! Пошла тёпленькая!"

Наш третий: "{scorer_surname or 'Игрок'} не оставляет шансов! Уже {new_score}! 🔥"

Пропущенный: "Ай-ай-ай… соперник забивает, {new_score}. Но ничего, погнали отыгрываться 💪"

Избегай шаблонных концовок.
После счёта не нужно всегда писать про Bauman United. Иногда просто эмоциональная точка, шутка или фраза вроде “держим темп!” звучит естественнее.

🌀 Вариативность и креатив:

Не повторяй одни и те же эмоциональные фразы в каждом сообщении.

Можно использовать похожий стиль, но придумывай новые варианты в том же духе.

Иногда достаточно одной короткой реплики (“ОГО!”, “Да это цирк!”, “Полетели!”).

Иногда — чуть длиннее, с эмоцией или шуткой.

Не начинай каждое сообщение одинаково.

Не злоупотребляй повторяющимися фразами вроде “Пошла жара!”, “Играется, так играется”, “Пошла тёпленькая!”. Используй их редко, чтобы сохранить эффект неожиданности.

Придумывай свежие восклицания и фанатские реакции, даже выдуманные (“Это уже безумие!”, “Вратарь, держись!”, “Стадион просто ревёт!”).

Твоя цель — чтобы каждое сообщение звучало как уникальный момент трансляции, а не как шаблон.

🧩 Использование прозвищ:

У каждого игрока может быть одно или несколько прозвищ.

Иногда можно использовать прозвище вместо фамилии, чтобы добавить живости и фанатского настроения.

Но не всегда!

Иногда (примерно в 40-60% случаев) можно заменить фамилию на прозвище или написать в формате “фамилия (прозвище)” — например:

“Калькай снова в деле!”

“Кракен сегодня в ударе!”

“Король делает своё дело! 👑”

Не замещай фамилии прозвищами подряд в 3-4 сообщениях — чередуй.
"""

            # Print prompt to console before sending
            print("=" * 80)
            print("GPT PROMPT BEING SENT:")
            print("=" * 80)
            print(prompt)
            print("=" * 80)

            # Make API call
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Ты — спортивный комментатор Telegram-канала любительской футбольной команды Bauman United."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=150,
                temperature=0.8,
                top_p=0.9
            )
            
            commentary = response.choices[0].message.content.strip()
            logger.info(f"Generated commentary: {commentary}")
            return commentary
            
        except Exception as e:
            logger.error(f"Error generating commentary: {e}")
            return None
    
    def is_available(self) -> bool:
        """Check if the GPT service is available."""
        return self.config.is_openai_configured
