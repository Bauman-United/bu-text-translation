#!/usr/bin/env python3
"""
Тестовый скрипт для проверки генерации комментариев GPT.

Позволяет тестировать, какие фразы генерирует ChatGPT на промпт.
Можно отправлять заготовленные сообщения о счете с контекстом.
"""

import sys
from typing import List, Optional, Tuple
from services.gpt_service import GPTCommentaryService


def print_separator():
    """Печатает разделитель."""
    print("\n" + "=" * 80 + "\n")


def parse_score(score_str: str) -> Tuple[int, int]:
    """
    Парсит счет в формате X:Y или X-Y.
    
    Returns:
        (наши_голы, голы_соперника)
    """
    try:
        # Поддерживаем оба формата: двоеточие и дефис
        if ':' in score_str:
            parts = score_str.split(':')
        elif '-' in score_str:
            parts = score_str.split('-')
        else:
            raise ValueError("Счет должен быть в формате X:Y или X-Y")
        
        if len(parts) != 2:
            raise ValueError("Счет должен быть в формате X:Y или X-Y")
        
        our_goals = int(parts[0].strip())
        opponent_goals = int(parts[1].strip())
        return our_goals, opponent_goals
    except (ValueError, IndexError) as e:
        raise ValueError(f"Неверный формат счета: {score_str}. Используйте формат X:Y или X-Y") from e


def determine_goal_info(
    current_score: str,
    previous_score: Optional[str],
    scorer_surname: Optional[str]
) -> Tuple[str, bool, Optional[str]]:
    """
    Определяет информацию о голе по счету.
    
    Формат ввода: счет [фамилия]
    Примеры:
    - "1-0 Богомолов" или "1:0 Богомолов" - наш гол с фамилией
    - "1-1" или "1:1" - автоматическое определение по изменению счета
    
    Args:
        current_score: Текущий счет в формате X:Y или X-Y
        previous_score: Предыдущий счет в формате X:Y или X-Y, или None
        scorer_surname: Фамилия забившего (если указана)
    
    Returns:
        (счет, is_our_goal, scorer_surname)
    """
    our_current, opp_current = parse_score(current_score)
    
    # Если фамилия указана, это точно наш гол
    if scorer_surname:
        return current_score, True, scorer_surname
    
    # Если это первый счет
    if previous_score is None:
        # Если первая цифра > 0, значит это наш гол (но без фамилии)
        if our_current > 0:
            return current_score, True, None
        # Если вторая > 0, значит соперник
        elif opp_current > 0:
            return current_score, False, None
        else:
            return current_score, True, None  # По умолчанию считаем наши
    
    # Сравниваем с предыдущим счетом
    our_previous, opp_previous = parse_score(previous_score)
    
    our_increase = our_current - our_previous
    opp_increase = opp_current - opp_previous
    
    if our_increase > 0:
        # Первая цифра увеличилась - наши забили
        return current_score, True, None
    elif opp_increase > 0:
        # Вторая цифра увеличилась - соперник забил
        return current_score, False, None
    else:
        # Счет не изменился (странно, но на всякий случай)
        raise ValueError(f"Счет не изменился: {previous_score} -> {current_score}")


def parse_score_input(score_str: str, previous_score: Optional[str] = None) -> Tuple[str, bool, Optional[str]]:
    """
    Парсит ввод счета.
    
    Формат: счет [фамилия]
    Примеры:
    - "1-0 Богомолов" или "1:0 Богомолов" - наш гол с фамилией
    - "1-1" или "1:1" - автоматическое определение по изменению счета
    """
    parts = score_str.strip().split()
    if not parts:
        raise ValueError("Введите счет в формате X-Y или X:Y [фамилия]")
    
    score = parts[0]
    scorer_surname = parts[1] if len(parts) > 1 else None
    
    return determine_goal_info(score, previous_score, scorer_surname)


def interactive_mode():
    """Интерактивный режим для тестирования."""
    print("=" * 80)
    print("ТЕСТОВЫЙ СКРИПТ ДЛЯ ГЕНЕРАЦИИ КОММЕНТАРИЕВ GPT")
    print("=" * 80)
    print("\nРежим: Интерактивный")
    print("\nКоманды:")
    print("  <счет [фамилия]>      - Добавить счет (например: 1-0 Богомолов или 1-1)")
    print("                         Формат: X-Y или X:Y (первая цифра - наши голы, вторая - соперника)")
    print("                         Если фамилия указана - наш гол, иначе определяется автоматически")
    print("  'context'             - Показать текущий контекст")
    print("  'clear'               - Очистить контекст")
    print("  'exit' или 'quit'     - Выход")
    print("\n" + "=" * 80 + "\n")
    
    try:
        service = GPTCommentaryService()
    except ValueError as e:
        print(f"Ошибка: {e}")
        print("Убедитесь, что OPENAI_KEY настроен в .env файле")
        return
    
    previous_messages: List[str] = []
    previous_score: Optional[str] = None
    
    while True:
        try:
            user_input = input("\nВведите счет (или команду): ").strip()
            
            if not user_input:
                continue
            
            if user_input.lower() in ['exit', 'quit', 'q']:
                print("Выход из программы.")
                break
            
            if user_input.lower() == 'clear':
                previous_messages = []
                previous_score = None
                print("Контекст очищен.")
                continue
            
            if user_input.lower() == 'context':
                print("\nТекущий контекст:")
                if previous_messages:
                    for i, msg in enumerate(previous_messages, 1):
                        print(f"  {i}. {msg}")
                else:
                    print("  (контекст пуст)")
                continue
            
            # Парсим ввод
            try:
                score, is_our_goal, scorer_surname = parse_score_input(user_input, previous_score)
            except ValueError as e:
                print(f"Ошибка: {e}")
                continue
            
            print_separator()
            print(f"Генерация комментария для счета: {score}")
            print(f"Команда: {'Наша' if is_our_goal else 'Соперник'}")
            if scorer_surname:
                print(f"Забивший: {scorer_surname}")
            if previous_score:
                print(f"Предыдущий счет: {previous_score}")
            if previous_messages:
                print(f"Контекст: {len(previous_messages)} предыдущих сообщений")
            print_separator()
            
            # Генерируем комментарий
            commentary = service.generate_commentary(
                previous_messages=previous_messages.copy(),
                new_score=score,
                is_our_goal=is_our_goal,
                scorer_surname=scorer_surname
            )
            
            if commentary:
                print_separator()
                print("СГЕНЕРИРОВАННЫЙ КОММЕНТАРИЙ:")
                print("-" * 80)
                print(commentary)
                print("-" * 80)
                print_separator()
                
                # Добавляем в контекст
                add_to_context = input("Добавить это сообщение в контекст? (y/n, по умолчанию y): ").strip().lower()
                if add_to_context != 'n':
                    previous_messages.append(commentary)
                    previous_score = score
                    print(f"Добавлено в контекст. Всего сообщений: {len(previous_messages)}")
            else:
                print("Ошибка при генерации комментария.")
                
        except KeyboardInterrupt:
            print("\n\nПрервано пользователем. Выход.")
            break
        except Exception as e:
            print(f"Ошибка: {e}")


def test_mode():
    """Режим с заготовленными тестами."""
    print("=" * 80)
    print("ТЕСТОВЫЙ СКРИПТ ДЛЯ ГЕНЕРАЦИИ КОММЕНТАРИЕВ GPT")
    print("=" * 80)
    print("\nРежим: Тестовый (заготовленные сообщения)")
    print_separator()
    
    try:
        service = GPTCommentaryService()
    except ValueError as e:
        print(f"Ошибка: {e}")
        print("Убедитесь, что OPENAI_KEY настроен в .env файле")
        return
    
    # Заготовленные тестовые сценарии
    test_scenarios = [
        # Первый гол нашей команды
        {
            "score": "1:0",
            "is_our_goal": True,
            "scorer_surname": "Богомолов",
            "description": "Первый гол нашей команды"
        },
        # Гол соперника
        {
            "score": "1:1",
            "is_our_goal": False,
            "scorer_surname": None,
            "description": "Гол соперника"
        },
        # Дубль нашего игрока
        {
            "score": "2:1",
            "is_our_goal": True,
            "scorer_surname": "Богомолов",
            "description": "Дубль нашего игрока"
        },
        # Гол другого нашего игрока
        {
            "score": "3:1",
            "is_our_goal": True,
            "scorer_surname": "Писарев",
            "description": "Гол другого нашего игрока"
        },
        # Третий гол того же игрока (хет-трик)
        {
            "score": "4:1",
            "is_our_goal": True,
            "scorer_surname": "Богомолов",
            "description": "Хет-трик нашего игрока"
        },
    ]
    
    previous_messages: List[str] = []
    previous_score: Optional[str] = None
    
    for i, scenario in enumerate(test_scenarios, 1):
        print(f"\nТЕСТ {i}/{len(test_scenarios)}: {scenario['description']}")
        print(f"Счет: {scenario['score']}, Забивший: {scenario['scorer_surname'] or 'Соперник'}")
        if previous_messages:
            print(f"Контекст: {len(previous_messages)} предыдущих сообщений")
        print_separator()
        
        commentary = service.generate_commentary(
            previous_messages=previous_messages.copy(),
            new_score=scenario['score'],
            is_our_goal=scenario['is_our_goal'],
            scorer_surname=scenario['scorer_surname']
        )
        
        if commentary:
            print_separator()
            print("СГЕНЕРИРОВАННЫЙ КОММЕНТАРИЙ:")
            print("-" * 80)
            print(commentary)
            print("-" * 80)
            print_separator()
            
            # Добавляем в контекст
            previous_messages.append(commentary)
            previous_score = scenario['score']
        else:
            print("Ошибка при генерации комментария.")
        
        # Пауза между тестами (опционально)
        if i < len(test_scenarios):
            input("\nНажмите Enter для следующего теста...")
    
    print("\n" + "=" * 80)
    print("ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ")
    print("=" * 80)
    print(f"\nВсего сгенерировано сообщений: {len(previous_messages)}")
    print("\nФинальный контекст:")
    for i, msg in enumerate(previous_messages, 1):
        print(f"\n{i}. {msg}")


def main():
    """Главная функция."""
    if len(sys.argv) > 1 and sys.argv[1] == '--test':
        test_mode()
    else:
        interactive_mode()


if __name__ == "__main__":
    main()

