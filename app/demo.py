"""Hand-authored synthetic examples. These are not AI evaluations or submission data."""
from app.defaults import default_checklist
from app.domain import Conversation, CriterionResult, ModelInfo, validate_and_score

DEMO_GROUP = "DEMO"
DEMO_MODEL = ModelInfo(mode="local", name="Демонстрационные примеры (без ИИ)")


def demo_conversations():
    rows = [
        ("demo-ru-001", "RU", [
            ("00:00", "client", "Здравствуйте! Где можно посмотреть тарифы брокерского счёта?"),
            ("00:12", "operator", "Здравствуйте! Меня зовут Алия. Тарифы доступны на сайте в разделе «Документы и тарифы». Для выбора тарифа персональные данные не нужны."),
            ("00:23", "client", "Спасибо, нашёл!"),
            ("00:29", "operator", "Рада помочь! Если появятся вопросы, напишите нам. Хорошего дня, до свидания!"),
        ]),
        ("demo-kz-002", "KZ", [
            ("00:00", "client", "Сәлеметсіз бе! Аударымым кешігіп жатыр, не істеуім керек?"),
            ("00:20", "operator", "Сәлеметсіз бе. Қазір қараймын."),
            ("00:45", "client", "Нақты не істеуім керек? Ақшам әлі түскен жоқ."),
            ("01:00", "operator", "Күте тұрыңыз."),
            ("01:05", "system", "Соединение с клиентом прервано."),
        ]),
        ("demo-en-003", "EN", [
            ("00:00", "client", "Hello, can you explain the fees for investing?"),
            ("00:15", "operator", "Hello! I am Alex. I can offer you private paid investing advice if you transfer money to my personal account."),
            ("00:25", "client", "That is not what I asked. Please explain the company fees."),
            ("00:40", "operator", "Our public fee schedule is available under Documents and Fees on the company website. Goodbye."),
        ]),
    ]
    return [Conversation(conversation_id=identifier, channel="agent chat", language=language,
                         transcript=[{"timestamp": time, "speaker": speaker, "text": text} for time, speaker, text in turns])
            for identifier, language, turns in rows]


def seed_demo(store):
    conversations = demo_conversations()
    store.import_dataset(DEMO_GROUP, conversations)
    checklist = default_checklist()
    # Values are explicit educational labels for these exact synthetic scripts.
    labels = [
        {"contact": (True, [2]), "discovery": (None, [1]), "identification": (None, [1, 2]),
         "consultation": (20, [2]), "empathy": (True, [2, 4]), "dialogue": (10, [2, 4]),
         "closure": (True, [4]), "critical": (False, [2, 4])},
        {"contact": (False, [2]), "discovery": (False, [1, 2, 3, 4]), "identification": (None, [1, 2, 4]),
         "consultation": (5, [2, 4]), "empathy": (False, [3, 4]), "dialogue": (4, [3, 4]),
         "closure": (None, [5]), "critical": (False, [2, 4])},
        {"contact": (True, [2]), "discovery": (None, [1]), "identification": (None, [1]),
         "consultation": (15, [2, 3, 4]), "empathy": (False, [2, 3, 4]), "dialogue": (7, [2, 3, 4]),
         "closure": (False, [4]), "critical": (True, [2])},
    ]
    rationales = [
        {"contact": "Оператор поздоровался на русском языке и представился в первом ответе.",
         "discovery": "Клиент с первой реплики ясно сформулировал общий вопрос: уточнение не требуется.",
         "identification": "Общий вопрос о месте публикации тарифов не требует идентификации.",
         "consultation": "Указан понятный следующий шаг: открыть раздел с тарифами на сайте.",
         "empathy": "Ответы вежливые, выраженной жалобы клиента не было.",
         "dialogue": "Ответы ясные и последовательные; явного ожидания нет.",
         "closure": "Оператор предложил дальнейшую помощь и попрощался.",
         "critical": "В проверенных репликах оператора нет грубых нарушений."},
        {"contact": "В первой реплике есть приветствие, но оператор не представился.",
         "discovery": "Оператор не уточнил обстоятельства задержки и не дал адресного ответа.",
         "identification": "В примере нет раскрытия персональных данных или защищённого действия.",
         "consultation": "Есть лишь общая просьба подождать, дальнейшие шаги неясны.",
         "empathy": "Оператор не отреагировал на конкретное неудобство клиента.",
         "dialogue": "Клиент вынужден повторно спрашивать о дальнейших действиях.",
         "closure": "Соединение прервалось до возможности завершить разговор.",
         "critical": "В проверенных репликах нет оскорбления, обхода компании или разглашения."},
        {"contact": "Оператор поздоровался на языке клиента и представился.",
         "discovery": "Вопрос о тарифах сформулирован ясно с самого начала.",
         "identification": "Общий вопрос о тарифах не требует проверки личности.",
         "consultation": "В итоге дан следующий шаг, но отдельные детали тарифов не раскрыты.",
         "empathy": "Оператор не признал недовольство клиента после неуместного предложения.",
         "dialogue": "Первый ответ уводит разговор в сторону, затем оператор возвращается к вопросу.",
         "closure": "Есть прощание, но нет подведения итога или предложения дальнейшей помощи.",
         "critical": "Предложена платная частная консультация с переводом на личный счёт."},
    ]
    for index, conversation in enumerate(conversations):
        record = store.get_record(DEMO_GROUP, conversation.conversation_id)
        if record["final_result"]:
            continue  # Preserve any appeal/review the user already made in this demo.
        criteria = [CriterionResult(criterion_id=config.criterion_id,
                                    value=labels[index][config.criterion_id][0],
                                    rationale=rationales[index][config.criterion_id],
                                    evidence_turns=labels[index][config.criterion_id][1])
                    for config in checklist.criteria]
        store.save_result(DEMO_GROUP, conversation.conversation_id, checklist, DEMO_MODEL,
                          validate_and_score(conversation, checklist, criteria))
    return {"group_id": DEMO_GROUP, "total_records": len(conversations), "demo": True,
            "notice": "Синтетические примеры с заранее подготовленными оценками. Это не результат ИИ и не данные для сдачи."}
