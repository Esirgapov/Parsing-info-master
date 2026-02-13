import base64
import json
import re
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# --- Selenium для работы с динамическими тестами ---
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


BASE_CATEGORY_URL = "https://info-master.uz/category/informatika-2/"


@dataclass
class AnswerOption:
    text: str 
    is_correct: bool
    # Список URL картинок, привязанных к этому варианту (если есть)
    images: List[str]


@dataclass
class Question:
    text: str
    options: List[AnswerOption]
    # Сырые варианты (только текст вариантов, без флагов)
    variants: List[str]
    # Индексы правильных вариантов (0-based). В большинстве случаев здесь один элемент.
    correct_answer: List[int]
    # Картинки, относящиеся к самому вопросу
    images: List[str]


@dataclass
class Test:
    title: str
    url: str
    questions: List[Question]


# ===== HTTP (статичные страницы) =====


def fetch_html(url: str) -> str:
    """Download raw HTML of a page."""
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return resp.text


def get_test_links() -> List[str]:
    """
    Collect all test URLs from the Informatika category.

    On the site the post titles with links live in elements
    with CSS classes:
      font130 mt0 mb10 mobfont120 lineheight25
    We select <h2> with these classes and then grab the inner <a href>.
    """
    links: List[str] = []
    page = 1

    while True:
        url = BASE_CATEGORY_URL if page == 1 else f"{BASE_CATEGORY_URL}page/{page}/"
        html = fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")

        # Each test title block: <h2 class="font130 mt0 mb10 mobfont120 lineheight25"><a href="...">...</a></h2>
        for h2 in soup.select("h2.font130.mt0.mb10.mobfont120.lineheight25 a"):
            href = h2.get("href")
            if href and href not in links:
                links.append(href)

        # Pagination: if there is no "Keyingi sahifa" (Next page) link, stop.
        has_next = soup.find("a", string=lambda s: s and "Keyingi sahifa" in s)
        if not has_next:
            break

        page += 1

    return links[:2]


QUIZ_OPTIONS_RE = re.compile(
    r"window\.quizOptions_\d+\s*\[\s*'(\d+)'\s*\]\s*=\s*'([^']+)'"
)


# ===== Selenium (динамические тесты) =====

def build_driver() -> webdriver.Chrome:
    """Создать headless Chrome с помощью webdriver_manager."""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    return driver


def _extract_quiz_options(html: str) -> Dict[str, Dict]:
    """
    Вытаскиваем объект window.quizOptions_XXXX по каждому question-id.

    На странице встречаются строки вида:
        window.quizOptions_1851['52455'] = 'base64(...json...)';
    """
    result: Dict[str, Dict] = {}
    for qid, b64 in QUIZ_OPTIONS_RE.findall(html):
        try:
            decoded = base64.b64decode(b64).decode("utf-8")
            data = json.loads(decoded)
        except Exception:
            continue
        result[qid] = data
    return result


def _parse_quiz_from_html(url: str, html: str) -> Test:
    """
    Парсинг HTML викторины Quiz Maker БЕЗ кликов по вариантам.

    Мы читаем:
      - все блоки .step[data-question-id]
      - текст вопроса из .ays_quiz_question
      - текст вариантов из .ays-quiz-answers
      - правильные ответы берём из window.quizOptions_XXXX
    """
    soup = BeautifulSoup(html, "html.parser")

    # Заголовок теста
    title_el = soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else url

    quiz_options = _extract_quiz_options(html)

    questions: List[Question] = []

    for step in soup.select("div.step[data-question-id]"):
        qid = step.get("data-question-id")
        cfg = quiz_options.get(qid, {})
        q_type = cfg.get("question_type") or step.get("data-type") or "radio"

        # Текст вопроса
        q_block = step.select_one(".ays_quiz_question")
        if q_block:
            q_text = q_block.get_text(" ", strip=True)
        else:
            q_text = ""

        # Картинки, прикреплённые к вопросу (например, схема, рисунок и т.п.)
        question_images: List[str] = []
        if q_block:
            for img_el in q_block.select("img"):
                src = img_el.get("src")
                if src:
                    question_images.append(urljoin(url, src))

        options: List[AnswerOption] = []

        # Радио / чекбокс – обычные варианты
        if q_type in ("radio", "checkbox"):
            correct_map: Dict[str, str] = cfg.get("question_answer", {})

            for field in step.select(".ays-quiz-answers .ays-field"):
                input_el = field.select_one("input[id^='ays-answer-']")
                if not input_el:
                    continue

                answer_id = input_el.get("value") or ""
                input_id = input_el.get("id") or ""

                label_el = field.select_one(f"label[for='{input_id}']")
                text = ""
                img_urls: List[str] = []
                if label_el:
                    # Берём только текст, без вложенных label для картинок
                    text = label_el.get_text(" ", strip=True)
                    # Ищем все <img> внутри этого label (варианты с картинками)
                    for img_el in label_el.select("img"):
                        src = img_el.get("src")
                        if src:
                            img_urls.append(urljoin(url, src))

                is_correct = False
                if correct_map:
                    val = str(correct_map.get(answer_id, "0")).lower()
                    is_correct = val in ("1", "true")

                options.append(
                    AnswerOption(
                        text=text,
                        is_correct=is_correct,
                        images=img_urls,
                    )
                )

        # Короткий текстовый ответ — кладём правильный ответ как единственный вариант
        elif q_type == "short_text":
            ans = cfg.get("question_answer", "")
            if ans:
                options.append(
                    AnswerOption(
                        text=str(ans),
                        is_correct=True,
                        images=[],
                    )
                )

        # Соответствие (matching) — собираем пары "текст -> номер"
        elif q_type == "matching":
            # question_answer: {позиция: answer_id}
            ans_map: Dict[str, str] = cfg.get("question_answer", {})
            # строим обратное: answer_id -> позиция
            inv_ans_map: Dict[str, str] = {
                v: k for k, v in ans_map.items()
            }

            for opt in step.select(".ays-matching-field .ays-matching-field-option"):
                choice_el = opt.select_one(".ays-matching-field-choice")
                match_el = opt.select_one(".ays-matching-field-match")
                if not choice_el or not match_el:
                    continue
                choice_text = choice_el.get_text(" ", strip=True)
                img_urls: List[str] = []
                for img_el in choice_el.select("img"):
                    src = img_el.get("src")
                    if src:
                        img_urls.append(urljoin(url, src))

                answer_id = match_el.get("data-answer-id", "")
                pos = inv_ans_map.get(answer_id)
                # Сохраняем как "текст -> номер" и помечаем как корректное соответствие
                if pos is not None:
                    text = f"{choice_text} -> {pos}"
                else:
                    text = choice_text
                options.append(
                    AnswerOption(
                        text=text,
                        is_correct=True,
                        images=img_urls,
                    )
                )

        # Собираем variants и индексы правильных ответов
        variants = [opt.text for opt in options]
        correct_idx = [i for i, opt in enumerate(options) if opt.is_correct]

        questions.append(
            Question(
                text=q_text,
                options=options,
                variants=variants,
                correct_answer=correct_idx,
                images=question_images,
            )
        )

    return Test(title=title, url=url, questions=questions)


def parse_test_page_dynamic(url: str, driver: webdriver.Chrome) -> Test:
    """
    Парсинг одной страницы теста через Selenium.

    Для Quiz Maker на info-master.uz достаточно:
      1. Открыть страницу.
      2. Дождаться появления контейнера викторины.
      3. Взять driver.page_source и распарсить всё через BeautifulSoup +
         window.quizOptions_XXXX (без кликов по вариантам).
    """
    driver.get(url)
    wait = WebDriverWait(driver, 20)

    try:
        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".ays-quiz-container")
            )
        )
    except TimeoutException:
        # Викторина не прогрузилась
        html = driver.page_source
        return _parse_quiz_from_html(url, html)

    html = driver.page_source
    return _parse_quiz_from_html(url, html)


def main() -> None:
    # 1) Get all test URLs from Informatika category
    test_links = get_test_links()
    print(f"Found {len(test_links)} tests.")

    all_tests: List[Test] = []

    driver = build_driver()
    try:
        for link in test_links:
            print(f"Parsing test (dynamic): {link}")
            test = parse_test_page_dynamic(link, driver)
            all_tests.append(test)
    finally:
        driver.quit()

    # 2) Save everything to JSON
    data = [asdict(t) for t in all_tests]
    with open("tests.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("Saved parsed tests to tests.json")


if __name__ == "__main__":
    main()

