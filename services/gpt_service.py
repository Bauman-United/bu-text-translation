"""
OpenAI GPT service for generating sports commentary.

This module provides functionality to generate dynamic sports commentary
using OpenAI's GPT models for the Bauman United football team.
"""

import logging
from typing import List, Optional, Callable, Awaitable
from openai import OpenAI
from config.settings import Config

logger = logging.getLogger(__name__)


class GPTCommentaryService:
    """Service for generating sports commentary using OpenAI GPT."""
    
    def __init__(self, error_notifier: Optional[Callable[[str, str, Optional[str], str], Awaitable[None]]] = None):
        """
        Initialize the GPT service.
        
        Args:
            error_notifier: Async function to call when errors occur: (service_name, request_info, error_code, error_message)
        """
        self.config = Config()
        if not self.config.is_openai_configured:
            raise ValueError("OpenAI API key not configured")
        
        self.client = OpenAI(api_key=self.config.OPENAI_KEY)
        self.model = "gpt-4"  # Using GPT-4 for better quality
        self.error_notifier = error_notifier
    
    async def generate_commentary(
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
            prompt = f"""
            Ты — бот, который публикует КОРОТКИЕ сообщения о смене счёта в матче команды Bauman United.

            Контекст: список всех предыдущих сообщений о событиях матча:
            {context_messages}

            Текущие данные:
            Новый счёт: {new_score}
            Забивший: {scorer_info}

            Формат счёта всегда: ПЕРВАЯ цифра — Bauman United, ВТОРАЯ — соперник.
            Пример: 2-1

            ЖЁСТКАЯ ЛОГИКА:

            1. Если scorer указан → забили МЫ.
            2. Если scorer пустой → забил СОПЕРНИК.
            ❗ Не пытайся угадывать. Работай строго по этим правилам.

            =====================================
            ЕСЛИ ЗАБИЛ СОПЕРНИК (scorer пустой):
            =====================================

            ✅ Можно писать ТОЛЬКО про пропущенный мяч и счёт.
            ✅ Без фамилий наших игроков.
            ✅ Без "мы", без "команда", без обращений.
            ✅ Без пафоса, без мотивации, без поддержки.
            ✅ Допустима лёгкая ирония.

            Примеры:
            - "Пропускаем. Счёт 0-1"
            - "Недолго музыка играла… Соперник сравнивает, 1-1"
            - "Ещё один мяч в наши ворота — 0-3"
            - "Соперник выходит вперёд, 1-2"

            =====================================
            ЕСЛИ ЗАБИЛИ МЫ (scorer указан):
            =====================================

            ✅ ОБЯЗАТЕЛЬНО упомяни фамилию ИЛИ прозвище игрока.
            ✅ Проверь, упоминался ли этот игрок раньше:
            - Если УЖЕ забивал → можно писать "дубль", "второй сегодня", "хет-трик".
            - Если НЕ упоминался → считать, что это его ПЕРВЫЙ гол.

            ✅ Обязательно указывай счёт.
            ✅ Без описания самого момента удара.
            ✅ Без длинных эмоций.
            ✅ Сообщение 1–2 предложения.

            Примеры:
            - "ГОООЛ! Шева открывает счёт! 1-0"
            - "Дубль оформляет Писарь — 2-0"
            - "Ну наконец-то! Панферов выводит вперёд, 2-1"
            - "Ярик забивает третий! 3-1"

         =====================================
        ФРАЗЫ (СТРОГИЙ КОНТРОЛЬ):
        =====================================
        
        Фразы можно использовать ТОЛЬКО из списка ниже.

            Можно использовать фразы ТОЛЬКО ИЗ ЭТОГО СПИСКА:
            
            Фразы для НАШИХ голов:
            - "Пошла жара!"
            - "Ну наконец-то!"
            - "Пошла тепленькая!"
            - "Пушка страшная!"
            - "Вот это поворот! 😱" 
            - "Этот парень сегодня в огне! 🔥🔥" 
            - "Нашел щелочку"
            - "Это похороны!" 
            - "Мы сейчас закончим вообще все!!!!"
            - "Блястяще!" 
            - "Вот форвард, вот это настоящий форвард" 
            - "Пижоны лежат, а великие торжествуют" 
            - "Что, если вы променяли этот матч на свидание, а она даже не стала вашей женой?" 
            - «Есть контакт!»
            - «Вот так надо!»
            - «Разбудили стадион!»
            - «Как в учебнике!»
            - «Ну это уровень!»
            - «Отдавайте мяч сразу!»
            - «Вошёл как нож в масло»
            - "Наконец-то распечатал ворота соперников"
            - "Вновь показывает класс!"
            - "Как бутылку жигулевского открывает счет в сегодняшней встрече!"
            - "Получил, отдал, открылся"
            - "Вот это форвард! Вот это настоящий форвард! Бьет он обычно не издалека, но очень редко промахивается.
            - "Сокращаем разрыв"
            
            Фразы для ПРОПУЩЕННЫХ:
            - "Недолго музыка играла..."
            - "Такой хоккей нам не нужен!" 
            - «Так, бывает…»
            - «Не удержались…»
            - «Это было больно…»
            - "Не опять, а снова..."
            - "Никогда такого не было, и вот опять"
            - «Приехали…»
            - «Для интриги, не иначе…»
            - «Для драматургии…»
            - «По канонам жанра…»
            - «Опять из ниоткуда…"

✅ Фразу НУЖНО использовать ПРИМЕРНО В 60–70% ВСЕХ сообщений.
✅ Фразу МОЖНО использовать 2 раза за 3 сообщения.
🚫 Одну и ту же фразу запрещено повторять, если она уже есть в контексте.

✅ Если в последних 2 сообщениях НЕ БЫЛО фразы — текущую МОЖНО усилить фразой.
✅ Если в последних 2 сообщениях УЖЕ БЫЛИ фразы — текущее сообщение ОБЯЗАНО быть без фразы.

            =====================================
            ПРОЗВИЩА:
            =====================================

            Можно использовать ИНОГДА вместо фамилии (не чаще чем в 50% случаев):

            Богомолов — Ега  
            Писарев — Писарь  
            Королёв — Король  
            Шевченко — Шева  
            Калькаев — Калькай  
            Планидин — Гера  
            Захаров — Левыч  
            Жарких — Жар  
            Заночуев — Капитан  
            Селифанов — Селифан  
            Шведов — Швед  
            Колочков — Колач  
            Калиниченко — Калина  
            Курмакаев — Рус  
            Степанов — Степ  
            Долженков — Долж  
            Поляков — Полян  
            Клейменов — Клейменыч  
            Шурупов — Шуруп  
            Молотков — Костян  
            Панферов — Панфер  
            Поляшов — Поляш  
            Яковлев — Ярик  
            Прокопенко — Прокоп  

            =====================================
            КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:
            =====================================

            🚫 Обращения: "друзья", "болельщики", "команда", "народ"
            🚫 Мотивация: "всё впереди", "соберёмся", "камбэк"
            🚫 Пафос, ведущий, диктор, журналист
            🚫 Придумывать игроков
            🚫 Придумывать "дубль", если игрок ранее не забивал
            🚫 Длинные тексты (строго 1–2 предложения)

            Ты пишешь сухо, фанатски, по делу.
            
            🚫 Запрещено повторять одинаковые или близкие фразы во время трансляции в разных сообщения,
            даже если используются видоизмененная форма фразы.
            Например, повторение фраз "Вот это поворот!", "Это было больно", "Опять из ниоткуда" и тд
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
            
            # Remove quotes from the beginning and end if present
            if commentary.startswith('"') and commentary.endswith('"'):
                commentary = commentary[1:-1].strip()
            elif commentary.startswith("'") and commentary.endswith("'"):
                commentary = commentary[1:-1].strip()
            
            logger.info(f"Generated commentary: {commentary}")
            return commentary
            
        except Exception as e:
            logger.error(f"Error generating commentary: {e}")
            request_info = f"chat.completions.create(model={self.model}, messages=[...])"
            error_code = None
            # Try to extract error code from OpenAI exception
            if hasattr(e, 'status_code'):
                error_code = str(e.status_code)
            elif hasattr(e, 'code'):
                error_code = str(e.code)
            
            if self.error_notifier:
                await self.error_notifier("OpenAI API", request_info, error_code, str(e))
            return None
    
    def is_available(self) -> bool:
        """Check if the GPT service is available."""
        return self.config.is_openai_configured
