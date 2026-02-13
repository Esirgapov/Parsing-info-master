Attestatsiya parser
===================

This small project demonstrates how to parse tests from the
`Informatika` category on `info-master.uz`.

Overview
--------

- **Step 1**: collect all test URLs from
  `https://info-master.uz/category/informatika-2/`.
- **Step 2**: for each test page, try to extract questions,
  variants, and correct answers from the HTML.

The second step is highly dependent on the real HTML structure of the
test pages. You will need to inspect one test page in a browser
(`view-source:` works fine) and adjust the CSS selectors in
`main.py` inside `parse_test_page_static`.

Usage
-----

1. Create a virtual environment (optional but recommended).
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Run the parser:

   ```bash
   python main.py
   ```

4. The collected data will be saved to `tests.json`.

Notes
-----

- The site shows questions using JavaScript after pressing
  "Start test". If the questions are loaded through an AJAX
  request instead of being present in the static HTML, you will
  need either:

  - to mimic that AJAX request in Python (look at the Network tab
    in DevTools and copy its URL and parameters), or
  - to use Selenium/Playwright to run a real browser, click
    "Start test", and then read `driver.page_source`.

  Once you know how the questions appear in the HTML, update
  `parse_test_page_static` accordingly.

